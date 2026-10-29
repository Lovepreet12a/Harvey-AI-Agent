"""Bounded, persistent research jobs. This module has no outreach transports."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import aiosqlite
import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from harvey.collectors.discover import is_junk, normalize_domain, business_name
from harvey.config import load_env
from harvey.models import Company
from harvey.pidfile import hold_loop_lock, release_loop_lock

logger = logging.getLogger('harvey.research')
AGENT = 'MowzivaResearch/1.0'
ACTIVE = ('queued', 'running', 'cancelling')
LABELS = {'discovered': 'Discovered', 'researching': 'Researching',
          'research_incomplete': 'Research Incomplete', 'verified': 'Verified',
          'qualified': 'Qualified', 'draft_ready': 'Draft Ready',
          'needs_human_review': 'Needs Human Review', 'do_not_contact': 'Do Not Contact'}
SEGMENTS = ('property management', 'condominium management', 'townhouse management',
            'commercial property management', 'landlord', 'retail plaza', 'office building',
            'clinic', 'daycare', 'warehouse')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS research_jobs (
 id TEXT PRIMARY KEY, status TEXT NOT NULL, config TEXT NOT NULL, summary TEXT NOT NULL,
 message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_items (
 job_id TEXT NOT NULL, company_id TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL,
 retryable INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
 PRIMARY KEY(job_id,company_id));
CREATE TABLE IF NOT EXISTS research_leads (
 company_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'discovered',
 reason TEXT NOT NULL DEFAULT '', retryable INTEGER NOT NULL DEFAULT 0,
 sources TEXT NOT NULL DEFAULT '[]', contacts TEXT NOT NULL DEFAULT '[]',
 updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_cache (
 key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS research_schedule (
 id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, config TEXT NOT NULL,
 interval_minutes INTEGER NOT NULL, next_run REAL NOT NULL);
'''


def stamp():
    return datetime.now(timezone.utc).isoformat()


class BatchRequest(BaseModel):
    provider: str = 'existing'
    segments: list[str] = Field(default_factory=lambda: ['property management', 'condominium management'],
                                min_length=1, max_length=5)
    limit: int = Field(default=25, ge=1, le=50)
    lead_ids: list[str] = Field(default_factory=list, max_length=50)
    use_codex: bool = True
    max_codex_reviews: int = Field(default=10, ge=0, le=50)
    resolve_websites: bool = False
    include_airdrie: bool = False
    max_search_calls: int = Field(default=6, ge=0, le=20)
    max_estimated_cost: float = Field(default=0.01, ge=0, le=0.10)

    @field_validator('provider')
    @classmethod
    def provider_valid(cls, value):
        if value not in ('existing', 'serper', 'osm'):
            raise ValueError('Choose existing, serper or osm. Serper requires its own API credits.')
        return value

    @field_validator('segments')
    @classmethod
    def segments_valid(cls, values):
        if any(v not in SEGMENTS for v in values):
            raise ValueError('Choose supported business categories')
        return list(dict.fromkeys(values))


class ScheduleRequest(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=1440, ge=60, le=10080)
    batch: BatchRequest = Field(default_factory=BatchRequest)


class ResearchError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def identity_matches(name, text):
    generic = {'the', 'inc', 'ltd', 'limited', 'corp', 'company', 'management', 'property',
               'properties', 'commercial', 'calgary', 'condominium', 'services', 'group',
               'rental', 'rentals', 'real', 'estate', 'and', 'office'}
    tokens = [t for t in re.findall(r'[a-z0-9]+', name.lower()) if len(t) >= 3 and t not in generic]
    words = set(re.findall(r'[a-z0-9]+', text.lower()))
    return bool(tokens) and all(t in words for t in tokens[:3])


def in_area(row, include_airdrie=False):
    text = (row.get('evidence') or '').lower()
    location = (row.get('location') or '').lower()
    # A search phrase is not evidence; Calgary must occur on a checked official page.
    if 'airdrie' in location and 'calgary' not in location and not include_airdrie:
        return False
    return 'calgary' in text or (include_airdrie and 'airdrie' in text)


class SourceResearch:
    """One sequential worker, host pacing, cached reads and bounded retries.

    Public sites use Harvey's IP-pinned transport; only fixed documented APIs use
    the environment proxy. No browser automation, challenge solving or proxies
    to circumvent an upstream restriction.
    """
    def __init__(self, workspace, factory):
        self.workspace = workspace
        self.factory = factory
        self.delay = 2.0
        self.retry_base = 2.0
        self.max_wait = 30.0
        self.next_host = {}
        self.cooldown = {}
        self.search_calls = 0
        self.options = BatchRequest()

    async def cached(self, key):
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            row = await (await db.execute('SELECT payload FROM research_cache WHERE key=? AND expires>?',
                                         (key, time.time()))).fetchone()
        return json.loads(row[0]) if row else None

    async def cache(self, key, value, ttl=86400):
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            await db.execute('INSERT OR REPLACE INTO research_cache VALUES (?,?,?)',
                             (key, json.dumps(value), time.time()+ttl))
            await db.commit()

    async def pace(self, host):
        saved = await self.cached('cooldown:'+str(host))
        if saved:
            self.cooldown[host] = max(self.cooldown.get(host, 0), saved['until'])
        wait = self.cooldown.get(host, 0)-time.time()
        if wait > self.max_wait:
            raise ResearchError('Provider cooldown active; retry later, not with another mirror or identity.', True)
        delay = max(0, self.next_host.get(host, 0)-time.monotonic(), wait)
        if delay:
            await asyncio.sleep(delay)
        self.next_host[host] = time.monotonic()+self.delay

    def retry_after(self, response, attempt):
        raw = response.headers.get('retry-after', '')
        try:
            seconds = float(raw)
        except ValueError:
            try:
                seconds = parsedate_to_datetime(raw).timestamp()-time.time()
            except (ValueError, TypeError, OverflowError):
                seconds = 0
        return max(0, seconds, self.retry_base * 2**attempt)

    async def request(self, client, method, url, *, paid=False, **kwargs):
        host = urlsplit(url).hostname
        for attempt in range(3):
            await self.pace(host)
            if paid:
                cap = min(self.options.max_search_calls,
                          int((self.options.max_estimated_cost+1e-9)/0.001))
                if self.search_calls >= cap:
                    raise ResearchError('Serper request/estimated-credit cap reached. Increase the explicit cap or retry another batch.')
                self.search_calls += 1
            try:
                async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
                    data, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 1_000_000:
                            raise ResearchError('Source exceeds the 1 MB research limit; review manually.')
                        data.append(chunk)
                    result = {'status': response.status_code, 'url': str(response.url),
                              'text': b''.join(data).decode('utf-8', errors='replace'),
                              'type': response.headers.get('content-type', ''),
                              'location': response.headers.get('location', ''), 'checked_at': stamp()}
                    if response.status_code in (429, 502, 503, 504):
                        delay = self.retry_after(response, attempt)
                        self.cooldown[host] = time.time()+delay
                        await self.cache('cooldown:'+str(host), {'until': self.cooldown[host]}, ttl=delay)
                        if attempt == 2 or delay > self.max_wait:
                            raise ResearchError(f'HTTP {response.status_code}: provider is busy/rate-limited; '
                                                'retry later. Retry-After is respected; no mirror rotation.', True)
                        await asyncio.sleep(delay)
                        continue
                    if response.status_code in (401, 403, 406):
                        raise ResearchError(f'HTTP {response.status_code}: access denied or provider restriction. '
                                            'Check credentials/network/provider guidance; no automatic bypass.')
                    return result
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                # A paid API may already have charged for an ambiguous response. Never replay it.
                if paid or attempt == 2:
                    raise ResearchError(f'{type(exc).__name__}: source unavailable; check network access and retry later.', True) from exc
                await asyncio.sleep(self.retry_base * 2**attempt)
            except httpx.HTTPError as exc:
                raise ResearchError(f'{type(exc).__name__}: public-source security or network check failed.') from exc
        raise ResearchError('Source retries exhausted.', True)

    async def robots(self, client, url):
        parts = urlsplit(url)
        origin = f'{parts.scheme}://{parts.netloc}'
        key = 'robots:'+origin
        result = await self.cached(key)
        if result is None:
            from harvey.mowziva import public_url
            robots_url = origin+'/robots.txt'
            for _ in range(6):
                public_url(robots_url)
                result = await self.request(client, 'GET', robots_url)
                if result['status'] not in (301, 302, 303, 307, 308):
                    break
                robots_url = urljoin(robots_url, result['location'])
            # Every redirect uses the same public-address/TLS checks; errors are not permission.
            if result['status'] not in (200, 404, 410):
                raise ResearchError('robots.txt could not be checked; review the source manually.')
            await self.cache(key, result, ttl=3600)
        parser = RobotFileParser()
        parser.parse(result['text'].splitlines() if result['status'] == 200 else [])
        if result['status'] == 200 and not parser.can_fetch(AGENT, url):
            raise ResearchError('robots.txt disallows this research page; no automated retrieval attempted.')
        rate = parser.request_rate(AGENT)
        delay = max(parser.crawl_delay(AGENT) or 0, rate.seconds/rate.requests if rate and rate.requests else 0)
        if delay:
            self.next_host[parts.hostname] = max(self.next_host.get(parts.hostname, 0),
                                                 time.monotonic()+delay)

    async def page(self, url):
        try:
            return await self._page(url)
        except httpx.HTTPError as exc:
            raise ResearchError(f'{type(exc).__name__}: protected public-source connection failed; check network access.',
                                isinstance(exc, (httpx.NetworkError, httpx.TimeoutException))) from exc
        except ValueError as exc:
            raise ResearchError('Source URL is invalid or unsafe; review the public website URL.') from exc

    async def _page(self, url):
        from harvey.mowziva import public_url
        public_url(url)
        cached = await self.cached('page:'+url)
        if cached:
            return cached
        async with self.factory(timeout=15, headers={'User-Agent': AGENT}) as client:
            for _ in range(6):
                public_url(url)
                await self.robots(client, url)
                result = await self.request(client, 'GET', url)
                if result['status'] in (301, 302, 303, 307, 308):
                    url = urljoin(url, result['location'])
                    continue
                if result['status'] != 200:
                    raise ResearchError(f"HTTP {result['status']}: no usable public source page.")
                if 'html' not in result['type']:
                    raise ResearchError('Source is not an HTML business page.')
                soup = BeautifulSoup(result['text'], 'html.parser')
                for tag in soup(['script', 'style']):
                    tag.decompose()
                text = soup.get_text(' ', strip=True)
                if any(term in text[:1000].lower() for term in
                       ('verify you are human', 'checking your browser', 'captcha', 'access denied')):
                    raise ResearchError('Source presents an anti-bot challenge; review manually. No bypass attempted.')
                await self.cache('page:'+url, result)
                return result
        raise ResearchError('Too many redirects; review business identity manually.')

    async def search(self, query):
        key = 'serper:'+hashlib.sha256(query.encode()).hexdigest()
        cached = await self.cached(key)
        if cached is not None:
            return cached
        env = load_env()
        if not env.serper_api_key:
            raise ResearchError('Serper requires SERPER_API_KEY and provider credits. Configure it securely or choose existing leads/OSM.')
        async with httpx.AsyncClient(timeout=20) as client:
            response = await self.request(client, 'POST', 'https://google.serper.dev/search', paid=True,
                                          headers={'X-API-KEY': env.serper_api_key},
                                          json={'q': query, 'num': 10, 'gl': 'ca', 'hl': 'en'})
        if response['status'] != 200:
            raise ResearchError(f"Serper HTTP {response['status']}; check provider account and credits.")
        try:
            results = json.loads(response['text']).get('organic', [])
        except (ValueError, AttributeError) as exc:
            raise ResearchError('Serper returned an invalid response; check provider status.', True) from exc
        if not isinstance(results, list):
            raise ResearchError('Serper returned an invalid result list.', True)
        results = [r for r in results if isinstance(r, dict) and isinstance(r.get('link'), str)]
        await self.cache(key, results)
        return results

    async def osm(self, options):
        tags = {
            'property management': 'office=property_management',
            'condominium management': 'office=property_management',
            'townhouse management': 'office=property_management',
            'commercial property management': 'office=property_management',
            'retail plaza': 'shop=mall', 'office building': 'building=office',
            'clinic': 'amenity=clinic', 'daycare': 'amenity=childcare', 'warehouse': 'building=warehouse'}
        selected = list(dict.fromkeys(tags[v] for v in options.segments if v in tags))
        if not selected:
            raise ResearchError('OSM does not reliably identify landlords. Use Serper or sourced existing leads.')
        key = 'osm:'+','.join(sorted(selected))+':'+str(options.limit)
        payload = await self.cached(key)
        if payload is None:
            clauses = ''.join(f'nwr[{tag}](around:30000,51.0447,-114.0719);' for tag in selected)
            query = f'[out:json][timeout:25];({clauses});out center {options.limit};'
            async with httpx.AsyncClient(timeout=35) as client:
                result = await self.request(client, 'POST', 'https://overpass-api.de/api/interpreter',
                                            content=query.encode(), headers={'User-Agent': AGENT})
            if result['status'] != 200:
                raise ResearchError(f"OSM HTTP {result['status']}. Use Serper or existing leads; no mirror bypass.")
            try:
                payload = json.loads(result['text']).get('elements', [])
            except (ValueError, AttributeError) as exc:
                raise ResearchError('OSM returned an invalid response.', True) from exc
            if not isinstance(payload, list):
                raise ResearchError('OSM returned invalid map elements.', True)
            await self.cache(key, payload)
        ids = []
        from harvey.mowziva import public_url
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            data = entry.get('tags') or {}
            if not data.get('name'):
                continue
            city = data.get('addr:city', '')
            if 'airdrie' in city.lower() and not options.include_airdrie:
                continue
            website = data.get('website') or data.get('contact:website') or ''
            if website:
                try:
                    public_url(website)
                except ValueError:
                    website = ''
            location = ', '.join(v for v in (' '.join(filter(None, (data.get('addr:housenumber'),
                                     data.get('addr:street')))), city, data.get('addr:state')) if v)
            category = ('property management' if data.get('office') == 'property_management'
                        else data.get('amenity') or data.get('shop') or data.get('building') or '')
            source = f"https://www.openstreetmap.org/{entry.get('type')}/{entry.get('id')}"
            company = Company(name=data['name'], website=website, domain=normalize_domain(website),
                              location=location, industry=category, phone=data.get('phone') or data.get('contact:phone') or '',
                              source='research:osm', source_url=source, external_id=f"osm:{entry.get('type')}/{entry.get('id')}")
            ids.append(await self.workspace.state.add_company(company))
        return list(dict.fromkeys(ids))

    async def state(self, ident, status, reason='', retryable=False, **extra):
        if status not in LABELS:
            raise ValueError('Unknown research state')
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            await db.execute('INSERT OR IGNORE INTO research_leads(company_id,updated_at) VALUES (?,?)',
                             (ident, stamp()))
            fields = {'status': status, 'reason': reason, 'retryable': int(retryable), 'updated_at': stamp()}
            for key in ('sources', 'contacts'):
                if key in extra:
                    fields[key] = json.dumps(extra[key], ensure_ascii=False)
            await db.execute('UPDATE research_leads SET '+','.join(f'{k}=?' for k in fields)
                             +' WHERE company_id=?', (*fields.values(), ident))
            await db.commit()

    async def assign_website(self, ident, url):
        domain = normalize_domain(url)
        existing = await self.workspace.state.get_company_by_domain(domain)
        if existing and existing.id != ident:
            raise ResearchError(f'Official domain already belongs to another lead ({existing.id}); '
                                'duplicate candidate retained for human review, not merged or deleted.')
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            try:
                await db.execute('UPDATE companies SET website=?,domain=?,updated_at=? WHERE id=?',
                                 (url, domain, stamp(), ident))
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                raise ResearchError('Duplicate official domain; records retained for human review.') from exc

    async def resolve(self, row):
        if not self.options.resolve_websites:
            raise ResearchError('No official website recorded. Enable website resolution with Serper or add a documented website.')
        results = await self.search(f'"{row["name"]}" Calgary official website')
        for result in results[:3]:
            url = result['link']
            if is_junk(normalize_domain(url), result.get('title', '')):
                continue
            try:
                page = await self.page(url)
                soup = BeautifulSoup(page['text'], 'html.parser')
                if identity_matches(row['name'], soup.get_text(' ', strip=True)):
                    await self.assign_website(row['id'], page['url'])
                    return await self.workspace.lead(row['id'])
            except ResearchError as exc:
                if exc.retryable:
                    raise
                continue
        raise ResearchError('No official website could be corroborated from search results. Add a reliable source; no URL was guessed.')

    def extract(self, page):
        soup = BeautifulSoup(page['text'], 'html.parser')
        contacts, links = [], {}
        def add(kind, value):
            if value and not any(c['kind'] == kind and c['value'] == value for c in contacts):
                contacts.append({'kind': kind, 'value': value, 'source_url': page['url'],
                                 'observed_at': page['checked_at']})
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                values = json.loads(script.string or '')
            except (ValueError, TypeError):
                continue
            pending = values if isinstance(values, list) else [values]
            while pending:
                value = pending.pop()
                if not isinstance(value, dict):
                    continue
                pending.extend(value.get('@graph', []) if isinstance(value.get('@graph'), list) else [])
                if value.get('@type') == 'Person' and isinstance(value.get('name'), str) and isinstance(value.get('jobTitle'), str):
                    add('published_role', value['name'][:200]+' — '+value['jobTitle'][:200])
        for anchor in soup.find_all('a', href=True):
            href = anchor['href']
            if href.lower().startswith('mailto:'):
                email = href[7:].split('?')[0]
                if re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', email):
                    add('published_email', email)
                continue
            if href.lower().startswith('tel:'):
                add('published_phone', href[4:][:100])
                continue
            url = urljoin(page['url'], href)
            if urlsplit(url).scheme not in ('http', 'https') or normalize_domain(url) != normalize_domain(page['url']):
                continue
            label = anchor.get_text(' ', strip=True).lower()
            if any(t in label for t in ('contact', 'about', 'team', 'vendor', 'supplier', 'procurement')):
                links[url] = label
        for tag in soup(['script', 'style']):
            tag.decompose()
        text = soup.get_text(' ', strip=True)
        for email in re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text):
            add('published_email', email)
        roles = r'(?:Property Manager|Condominium Manager|Facilities Manager|Leasing Manager|Managing Broker|President|Owner)'
        for match in re.finditer(r'\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})\s*[,|–—:-]\s*('+roles+r')\b', text):
            add('published_role_context', match.group(0))
        return text[:16000], links, contacts

    async def verify(self, ident):
        row = await self.workspace.lead(ident)
        if row['stage'] == 'do_not_contact' or row['casl_basis'] == 'do_not_contact':
            raise ResearchError('Do Not Contact: no automated research attempted.')
        if not row['website'] or is_junk(row['domain']):
            row = await self.resolve(row)
        page = await self.page(row['website'])
        if normalize_domain(page['url']) != row['domain']:
            raise ResearchError('Business site redirects to a different domain; confirm identity manually.')
        text, links, contacts = self.extract(page)
        if len(text) < 80 or not identity_matches(row['name'], text):
            raise ResearchError('Official business identity could not be corroborated on the page. Research incomplete.')
        sources = [{'url': page['url'], 'checked_at': page['checked_at'], 'excerpt': text[:16000]}]
        errors = []
        enrichment_retryable = False
        # Bounded official-site enrichment only; no directories, personal profiles or form submission.
        for url in list(links)[:2]:
            if url == page['url']:
                continue
            try:
                extra = await self.page(url)
                if normalize_domain(extra['url']) != row['domain']:
                    raise ResearchError('Enrichment redirected outside the official domain.')
                more, more_links, found = self.extract(extra)
                sources.append({'url': extra['url'], 'checked_at': extra['checked_at'], 'excerpt': more})
                contacts.extend(found)
                links.update(more_links)
                text += '\n'+more
            except ResearchError as exc:
                errors.append(str(exc))
                enrichment_retryable = enrichment_retryable or exc.retryable
        fields = {'verification': 'website_checked', 'evidence': text[:40000],
                  'checked_at': page['checked_at']}
        for url, label in links.items():
            if 'contact' in label:
                fields.setdefault('contact_url', url)
            if any(t in label for t in ('vendor', 'supplier', 'procurement')):
                fields.setdefault('vendor_url', url)
        await self.workspace.save(ident, **fields)
        await self.state(ident, 'verified', '; '.join(errors), enrichment_retryable, sources=sources,
                         contacts=list({(c['kind'], c['value'], c['source_url']): c for c in contacts}.values()))
        return await self.workspace.lead(ident)


class ResearchService:
    def __init__(self, workspace):
        self.workspace = workspace
        self.source = workspace.researcher
        self.task = None
        self.scheduler_task = None
        self.start_lock = asyncio.Lock()
        self.active_id = None
        self.discovery_warnings = []
        self.process_lock = None

    async def init(self):
        lock_path = Path(self.workspace.state.db_path).with_suffix('.research.lock')
        self.process_lock = await asyncio.to_thread(hold_loop_lock, lock_path)
        if self.process_lock is None:
            raise RuntimeError('Another MOWZIVA service owns this database. Stop it before starting a second instance.')
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            await db.executescript(SCHEMA)
            # Never silently resume a paid request or inference after a crash.
            await db.execute("UPDATE research_jobs SET status='interrupted',message=?,updated_at=? "
                             "WHERE status IN ('queued','running','cancelling')",
                             ('Service restarted; review results and launch another batch explicitly.', stamp()))
            await db.execute("UPDATE research_items SET status='research_incomplete',message=?,retryable=1 "
                             "WHERE status='researching'", ('Service restarted; research incomplete.',))
            await db.execute("UPDATE research_leads SET status='research_incomplete',reason=?,retryable=1 WHERE status='researching'",
                             ('Service restarted; prior sources retained. Launch a new batch explicitly.',))
            await db.commit()
        self.scheduler_task = asyncio.create_task(self.scheduler())

    async def close(self):
        if self.scheduler_task and not self.scheduler_task.done():
            self.scheduler_task.cancel()
            try:
                await self.scheduler_task
            except asyncio.CancelledError:
                pass
        try:
            if self.task and not self.task.done():
                await self.cancel(self.active_id)
        finally:
            release_loop_lock(self.process_lock)
            self.process_lock = None

    async def update_job(self, ident, status, summary, message=''):
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            await db.execute('UPDATE research_jobs SET status=?,summary=?,message=?,updated_at=? WHERE id=?',
                             (status, json.dumps(summary), message, stamp(), ident))
            await db.commit()

    async def jobs(self):
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            db.row_factory = aiosqlite.Row
            records = [dict(r) for r in await (await db.execute(
                'SELECT * FROM research_jobs ORDER BY created_at DESC LIMIT 20')).fetchall()]
        for record in records:
            record['config'] = json.loads(record['config'])
            record['summary'] = json.loads(record['summary'])
        return records

    async def job(self, ident):
        records = await self.jobs()
        record = next((r for r in records if r['id'] == ident), None)
        if record is None:
            raise HTTPException(404, 'Research job not found in recent history')
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            db.row_factory = aiosqlite.Row
            record['items'] = [dict(r) for r in await (await db.execute(
                'SELECT i.*,c.name FROM research_items i JOIN companies c ON c.id=i.company_id '
                'WHERE job_id=? ORDER BY i.updated_at', (ident,))).fetchall()]
        return record

    async def start(self, options):
        async with self.start_lock:
            if (self.task and not self.task.done()) or self.workspace.busy.locked():
                raise HTTPException(409, 'Another research operation is active; wait or cancel it first')
            ident = uuid.uuid4().hex
            summary = dict(found=0, processed=0, verified=0, qualified=0, drafts=0, failed=0, retryable=0,
                           search_calls=0, total=0, codex_reviews=0, drafts_retained=0)
            async with aiosqlite.connect(self.workspace.state.db_path) as db:
                await db.execute('INSERT INTO research_jobs VALUES (?,?,?,?,?,?,?)',
                                 (ident, 'queued', options.model_dump_json(), json.dumps(summary), '', stamp(), stamp()))
                await db.commit()
            self.active_id = ident
            self.task = asyncio.create_task(self.run(ident, options, summary))
        return {'id': ident, 'status': 'queued'}

    async def cancel(self, ident):
        record = await self.job(ident)
        if record['status'] in ACTIVE and self.active_id == ident and self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        record = await self.job(ident)
        if record['status'] in ACTIVE:
            await self.update_job(ident, 'cancelled', record['summary'], 'Cancelled before additional work started.')
        return await self.job(ident)

    async def item(self, job, ident, status, reason='', retryable=False):
        await self.source.state(ident, status, reason, retryable)
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            await db.execute('INSERT OR REPLACE INTO research_items VALUES (?,?,?,?,?,?)',
                             (job, ident, status, reason, int(retryable), stamp()))
            await db.commit()

    async def discover(self, options):
        ids = []
        if options.provider == 'serper':
            areas = ['Calgary Alberta'] + (['Airdrie Alberta'] if options.include_airdrie else [])
            for area in areas:
                for segment in options.segments:
                    try:
                        results = await self.source.search(f'{segment} {area} official website')
                    except ResearchError as exc:
                        if ids:
                            self.discovery_warnings.append(str(exc))
                            return list(dict.fromkeys(ids))[:options.limit]
                        raise
                    for result in results:
                        url = result['link']
                        domain = normalize_domain(url)
                        if not domain or is_junk(domain, result.get('title', '')):
                            continue
                        from harvey.mowziva import public_url
                        try:
                            public_url(url)
                        except ValueError:
                            continue
                        company = Company(name=business_name(result.get('title', ''), domain),
                                          website=url, domain=domain, industry=segment, location=area,
                                          source='research:serper', source_url=url)
                        ids.append(await self.workspace.state.add_company(company))
                        if len(set(ids)) >= options.limit:
                            return list(dict.fromkeys(ids))
        elif options.provider == 'osm':
            ids.extend(await self.source.osm(options))
        if options.provider != 'existing':
            return list(dict.fromkeys(ids))[:options.limit]
        # Explicit existing-lead queue: no discovery credits are spent here.
        rows = await self.workspace.rows()
        valid = set(options.lead_ids)
        rows = [r for r in rows if (r['id'] in valid if valid else
                (not r['industry'] or any(segment in r['industry'].lower().replace('_', ' ')
                                         for segment in options.segments)))]
        for row in rows:
            if row['id'] not in ids:
                ids.append(row['id'])
        return ids[:options.limit]

    async def run(self, job, options, summary):
        current = None
        begun = False
        self.discovery_warnings = []
        try:
            async with self.workspace.busy:
                self.source.options = options
                self.source.search_calls = 0
                begun = True
                await self.update_job(job, 'running', summary, 'Discovering candidates')
                ids = await self.discover(options)
                summary.update(found=len(ids), total=len(ids))
                summary['failed'] += len(self.discovery_warnings)
                await self.update_job(job, 'running', summary, 'Researching official sources')
                codex_blocked = ''
                for ident in ids:
                    current = ident
                    row = await self.workspace.lead(ident)
                    if row['stage'] == 'do_not_contact' or row['casl_basis'] == 'do_not_contact':
                        await self.item(job, ident, 'do_not_contact', 'Suppressed; no research or draft attempted.')
                        summary['processed'] += 1
                        continue
                    if 'airdrie' in row['location'].lower() and 'calgary' not in row['location'].lower() and not options.include_airdrie:
                        await self.item(job, ident, 'research_incomplete', 'Airdrie is a separate service area and is not enabled; no research credits used.')
                        summary['processed'] += 1
                        summary['failed'] += 1
                        current = None
                        await self.update_job(job, 'running', summary)
                        continue
                    # Human pipeline decisions survive automated research.
                    old_evidence, old_review = row.get('evidence'), row.get('ai_review')
                    verified_this = False
                    try:
                        await self.item(job, ident, 'researching')
                        row = await asyncio.wait_for(self.source.verify(ident), timeout=180)
                        summary['verified'] += 1
                        verified_this = True
                        enrichment_reason = row.get('research_reason', '')
                        enrichment_retryable = row.get('retryable', False)
                        if enrichment_reason:
                            summary['failed'] += 1
                        from harvey.mowziva import category_evidence
                        if not in_area(row, options.include_airdrie) or not category_evidence(row):
                            raise ResearchError('Verified page lacks target-area/category evidence, or this is an excluded Airdrie-only lead. Research incomplete; no Codex call or draft.')
                        if row['qualified']:
                            summary['qualified'] += 1
                        await self.item(job, ident, 'qualified' if row['qualified'] else 'verified')
                        ai_message = ''
                        if options.use_codex and not (old_review and old_evidence == row.get('evidence')):
                            if summary['codex_reviews'] >= options.max_codex_reviews:
                                ai_message = 'Codex review cap reached; factual draft retained for human review. Increase the explicit cap for another batch.'
                            elif codex_blocked:
                                ai_message = codex_blocked
                            else:
                                try:
                                    summary['codex_reviews'] += 1
                                    await asyncio.wait_for(self.workspace.ai_review(ident, include_airdrie=options.include_airdrie), timeout=360)
                                except HTTPException as exc:
                                    ai_message = str(exc.detail)
                                    if exc.status_code == 503:
                                        codex_blocked = ai_message
                                except asyncio.TimeoutError:
                                    ai_message = 'Codex timed out; review manually or retry within subscription limits.'
                        # Do-not-contact is rechecked by draft(), even if the human edited during research.
                        row = await self.workspace.draft(ident, preserve_stage=True)
                        summary['drafts_retained' if row.get('draft_retained') else 'drafts'] += 1
                        final = 'needs_human_review' if ai_message or enrichment_reason or not row['qualified'] else 'draft_ready'
                        reason = '; '.join(filter(None, (ai_message, enrichment_reason)))
                        await self.item(job, ident, final, reason or 'Source-backed draft prepared; human/CASL approval required.', bool(ai_message or enrichment_retryable))
                        if ai_message or enrichment_retryable:
                            summary['retryable'] += 1
                    except (ResearchError, HTTPException, asyncio.TimeoutError) as exc:
                        retryable = getattr(exc, 'retryable', False) or isinstance(exc, asyncio.TimeoutError)
                        reason = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                        if isinstance(exc, asyncio.TimeoutError):
                            reason = 'Per-lead research time limit reached; retry later.'
                        row = await self.workspace.lead(ident)
                        suppressed = row['stage'] == 'do_not_contact' or row['casl_basis'] == 'do_not_contact'
                        if not verified_this and not suppressed:
                            await self.workspace.save(ident, verification='verification_failed')
                        await self.item(job, ident, 'do_not_contact' if suppressed else 'research_incomplete', reason, retryable)
                        summary['failed'] += 1
                        summary['retryable'] += int(retryable)
                    except Exception as exc:
                        logger.error('Unexpected research failure for lead %s (%s)', ident, type(exc).__name__)
                        await self.item(job, ident, 'research_incomplete', 'Unexpected research error. Review service logs/state; other leads continue.', True)
                        summary['failed'] += 1
                        summary['retryable'] += 1
                    summary['processed'] += 1
                    summary['search_calls'] = self.source.search_calls
                    current = None
                    await self.update_job(job, 'running', summary)
                await self.update_job(job, 'completed', summary,
                                      ('Research finished; all outreach remains unsent. ' if ids else
                                       'No candidates found. OSM coverage is sparse; use Serper or sourced existing leads. ')
                                      + '; '.join(self.discovery_warnings))
        except asyncio.CancelledError:
            if current:
                await self.item(job, current, 'research_incomplete', 'Research cancelled; previously saved sources retained.', True)
            summary['search_calls'] = self.source.search_calls if begun else 0
            await self.update_job(job, 'cancelled', summary, 'Current batch cancelled; saved schedule is unchanged. No more work in this batch.')
        except Exception as exc:
            summary['failed'] += 1
            summary['retryable'] += int(getattr(exc, 'retryable', False))
            summary['search_calls'] = self.source.search_calls
            reason = str(exc) if isinstance(exc, (ResearchError, HTTPException)) else 'Unexpected job failure; review before retrying.'
            await self.update_job(job, 'failed', summary, reason)

    async def schedule(self):
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            row = await (await db.execute('SELECT * FROM research_schedule WHERE id=1')).fetchone()
        if not row:
            return {'enabled': False, 'interval_minutes': 1440, 'batch': BatchRequest().model_dump(), 'next_run': None}
        return {'enabled': bool(row[1]), 'batch': json.loads(row[2]), 'interval_minutes': row[3], 'next_run': row[4]}

    async def set_schedule(self, settings):
        async with aiosqlite.connect(self.workspace.state.db_path) as db:
            await db.execute('INSERT OR REPLACE INTO research_schedule VALUES (1,?,?,?,?)',
                             (int(settings.enabled), settings.batch.model_dump_json(), settings.interval_minutes,
                              time.time()+settings.interval_minutes*60))
            await db.commit()
        return await self.schedule()

    async def tick(self):
        settings = await self.schedule()
        if not settings['enabled'] or settings['next_run'] > time.time():
            return
        if (self.task and not self.task.done()) or self.workspace.busy.locked():
            return
        # Set the next time before launch; no catch-up storm after downtime.
        await self.set_schedule(ScheduleRequest(**settings))
        await self.start(BatchRequest(**settings['batch']))

    async def scheduler(self):
        while True:
            await asyncio.sleep(30)
            try:
                await self.tick()
            except Exception as exc:
                logger.error('Research scheduler failed (%s)', type(exc).__name__)
                # A scheduler error must not stop the dashboard or turn on outreach.
                continue
