"""Batch research behavior with isolated SQLite, mock public sources and no outreach."""
import asyncio
import json
import time
from contextlib import asynccontextmanager

import aiosqlite
import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException

from harvey.mowziva import Workspace, LeadInput
from harvey.research import (BatchRequest, ResearchError, ResearchService, ScheduleRequest,
                             SourceResearch, identity_matches)
from tests.test_mowziva import client, add, fake_site  # noqa: F401


@pytest_asyncio.fixture
async def workspace(tmp_path):
    ws = Workspace(str(tmp_path / 'research.db'))
    await ws.init()
    ws.researcher.delay = 0
    ws.researcher.retry_base = 0
    return ws


def source_factory(handler):
    @asynccontextmanager
    async def factory(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            yield c
    return factory


async def lead(ws, name='Example Property Management', website='https://example.org', **extra):
    return await ws.add(LeadInput(name=name, website=website, source_url=website or 'https://www.openstreetmap.org/node/1',
                                 location='Calgary', industry='property management', **extra))


@pytest.mark.asyncio
async def test_rate_limit_backoff_is_bounded_and_cached(workspace, monkeypatch):
    calls, sleeps = [], []
    def handler(request):
        calls.append(str(request.url))
        if len(calls) < 3:
            return httpx.Response(429, headers={'Retry-After': '0'})
        return httpx.Response(200, text='ok')
    async def sleep(delay):
        sleeps.append(delay)
    monkeypatch.setattr('harvey.research.asyncio.sleep', sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        response = await workspace.researcher.request(c, 'GET', 'https://public.example/page')
    assert response['status'] == 200 and len(calls) == 3 and len(sleeps) == 2


@pytest.mark.asyncio
async def test_long_retry_after_stops_and_cools_host(workspace):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '3600'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(ResearchError, match='Retry-After') as caught:
            await workspace.researcher.request(c, 'GET', 'https://public.example/page')
        assert caught.value.retryable
        with pytest.raises(ResearchError, match='cooldown'):
            await workspace.researcher.request(c, 'GET', 'https://public.example/other')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_denials_are_not_retried(workspace):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(403)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(ResearchError, match='no automatic bypass') as caught:
            await workspace.researcher.request(c, 'GET', 'https://public.example/page')
    assert len(calls) == 1 and not caught.value.retryable


@pytest.mark.asyncio
async def test_page_robots_cache_contacts_and_evidence(workspace):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        return httpx.Response(200, headers={'Content-Type': 'text/html'}, text='''
          <h1>Example Property Management</h1><p>Example provides property management in Calgary, Alberta.
          Jane Smith — Property Manager</p><a href="mailto:office@example.org">Email office</a>
          <a href="/contact">Contact</a><a href="/vendors">Vendor registration</a>''')
    workspace.researcher.factory = source_factory(handler)
    ident = await lead(workspace)
    row = await workspace.researcher.verify(ident)
    first = len(calls)
    row = await workspace.researcher.verify(ident)
    assert len(calls) == first and calls.count('/robots.txt') == 1
    assert row['verification'] == 'website_checked'
    assert len(row['sources']) == 3
    assert all(s['checked_at'] and s['url'].startswith('https://example.org') for s in row['sources'])
    assert any(c['kind'] == 'published_email' and c['value'] == 'office@example.org' for c in row['professional_contacts'])
    assert any(c['kind'] == 'published_role_context' and 'Jane Smith' in c['value'] for c in row['professional_contacts'])


@pytest.mark.asyncio
async def test_robots_disallow_never_fetches_page(workspace):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, text='User-agent: *\nDisallow: /')
    workspace.researcher.factory = source_factory(handler)
    with pytest.raises(ResearchError, match='robots.txt disallows'):
        await workspace.researcher.page('https://example.org/private')
    assert paths == ['/robots.txt']


@pytest.mark.asyncio
async def test_missing_site_requires_explicit_provider_and_does_not_guess(workspace):
    ident = await lead(workspace, website='')
    workspace.researcher.options = BatchRequest(resolve_websites=False)
    with pytest.raises(ResearchError, match='No official website recorded'):
        await workspace.researcher.verify(ident)
    assert (await workspace.lead(ident))['website'] == ''


@pytest.mark.asyncio
async def test_official_resolution_checks_identity_and_dedups(workspace, monkeypatch):
    workspace.researcher.options = BatchRequest(resolve_websites=True)
    ident = await lead(workspace, website='')
    async def search(query):
        return [{'link': 'https://example.org', 'title': 'Example Property Management'}]
    async def page(url):
        return {'text': '<h1>Example Property Management</h1><p>Calgary property management company.</p>',
                'url': url, 'checked_at': '2026-10-29T00:00:00+00:00'}
    monkeypatch.setattr(workspace.researcher, 'search', search)
    monkeypatch.setattr(workspace.researcher, 'page', page)
    row = await workspace.researcher.resolve(await workspace.lead(ident))
    assert row['domain'] == 'example.org'
    second = await workspace.add(LeadInput(name='Example Property Management',
                     source_url='https://www.openstreetmap.org/node/2'))
    with pytest.raises(ResearchError, match='No official website'):
        await workspace.researcher.resolve(await workspace.lead(second))
    assert len(await workspace.rows()) == 2  # original evidence retained, no destructive merge


@pytest.mark.asyncio
async def test_search_budget_counts_retries_and_caches(workspace, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('harvey.research.load_env', lambda: SimpleNamespace(serper_api_key='test-only'))
    workspace.researcher.options = BatchRequest(max_search_calls=1)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'organic': [{'title': 'Example', 'link': 'https://example.org'}]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr('harvey.research.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    assert await workspace.researcher.search('query')
    assert await workspace.researcher.search('query')
    with pytest.raises(ResearchError, match='cap reached'):
        await workspace.researcher.search('other query')
    assert len(calls) == 1 and workspace.researcher.search_calls == 1


@pytest.mark.asyncio
async def test_serper_failure_and_missing_key_are_actionable(workspace, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('harvey.research.load_env', lambda: SimpleNamespace(serper_api_key=''))
    with pytest.raises(ResearchError, match='SERPER_API_KEY'):
        await workspace.researcher.search('query')


@pytest.mark.asyncio
async def test_batch_continues_after_missing_website_and_provider_failure(workspace, monkeypatch):
    missing = await lead(workspace, name='Missing Website', website='')
    good = await lead(workspace)
    async def verify(ident):
        if ident == missing:
            raise ResearchError('No official website found')
        await workspace.save(ident, verification='website_checked', evidence='Example property management in Calgary. ' * 8)
        return await workspace.lead(ident)
    async def unavailable(ident, **kwargs):
        raise HTTPException(503, 'Codex login needs renewal')
    monkeypatch.setattr(workspace.researcher, 'verify', verify)
    monkeypatch.setattr(workspace, 'ai_review', unavailable)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(lead_ids=[missing, good]))
    await service.task
    result = await service.job(job['id'])
    assert result['status'] == 'completed'
    assert result['summary']['processed'] == 2
    assert result['summary']['failed'] == 1 and result['summary']['drafts'] == 1
    assert {i['status'] for i in result['items']} == {'research_incomplete', 'needs_human_review'}
    assert 'DRAFT ONLY' in (await workspace.lead(good))['draft']


@pytest.mark.asyncio
async def test_cancellation_is_persistent_and_releases_lock(workspace, monkeypatch):
    ident = await lead(workspace)
    entered = asyncio.Event()
    async def slow(ident):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(workspace.researcher, 'verify', slow)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest())
    await asyncio.wait_for(entered.wait(), timeout=3)
    result = await service.cancel(job['id'])
    assert result['status'] == 'cancelled'
    assert not workspace.busy.locked()
    assert (await workspace.lead(ident))['research_status'] == 'research_incomplete'
    assert (await workspace.lead(ident))['draft'] == ''


@pytest.mark.asyncio
async def test_immediate_cancel_and_concurrent_job_rejected(workspace):
    service = ResearchService(workspace)
    job = await service.start(BatchRequest())
    with pytest.raises(HTTPException) as caught:
        await service.start(BatchRequest())
    assert caught.value.status_code == 409
    result = await service.cancel(job['id'])
    assert result['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_airdrie_excluded_unless_enabled_and_no_calgary_credit(workspace, monkeypatch):
    ident = await workspace.add(LeadInput(name='Example Property Management', website='https://example.org',
        source_url='https://example.org', location='Airdrie, Alberta', industry='property management'))
    async def verify(ident):
        await workspace.save(ident, verification='website_checked', evidence='Example property management in Airdrie. ' * 8)
        return await workspace.lead(ident)
    monkeypatch.setattr(workspace.researcher, 'verify', verify)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(use_codex=False))
    await service.task
    assert (await service.job(job['id']))['items'][0]['status'] == 'research_incomplete'
    assert (await workspace.lead(ident))['draft'] == ''
    job = await service.start(BatchRequest(use_codex=False, include_airdrie=True))
    await service.task
    assert (await service.job(job['id']))['summary']['drafts'] == 1
    assert not (await workspace.lead(ident))['qualified']  # separate from Calgary goal


@pytest.mark.asyncio
async def test_dnc_and_human_pipeline_preserved(workspace, monkeypatch):
    ident = await lead(workspace)
    await workspace.save(ident, stage='follow_up', notes='Owner notes', follow_up_on='2026-11-01')
    async def verify(ident):
        await workspace.save(ident, verification='website_checked', evidence='Example property management in Calgary. ' * 8)
        return await workspace.lead(ident)
    monkeypatch.setattr(workspace.researcher, 'verify', verify)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(use_codex=False))
    await service.task
    row = await workspace.lead(ident)
    assert row['stage'] == 'follow_up' and row['notes'] == 'Owner notes'
    await workspace.save(ident, stage='do_not_contact', casl_basis='do_not_contact')
    job = await service.start(BatchRequest())
    await service.task
    assert (await service.job(job['id']))['items'][0]['status'] == 'do_not_contact'
    with pytest.raises(HTTPException):
        await workspace.draft(ident, preserve_stage=True)


@pytest.mark.asyncio
async def test_schedule_is_opt_in_bounded_and_no_catch_up(workspace, monkeypatch):
    service = ResearchService(workspace)
    assert not (await service.schedule())['enabled']
    calls = []
    async def start(options):
        calls.append(options)
    monkeypatch.setattr(service, 'start', start)
    await service.set_schedule(ScheduleRequest(enabled=True, interval_minutes=60))
    async with aiosqlite.connect(workspace.state.db_path) as db:
        await db.execute('UPDATE research_schedule SET next_run=0')
        await db.commit()
    await service.tick()
    await service.tick()
    assert len(calls) == 1
    assert (await service.schedule())['next_run'] > time.time()
    await service.set_schedule(ScheduleRequest(enabled=False))
    await service.tick()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_restart_marks_jobs_interrupted_without_deleting_leads(workspace):
    ident = await lead(workspace)
    async with aiosqlite.connect(workspace.state.db_path) as db:
        await db.execute('INSERT INTO research_jobs VALUES (?,?,?,?,?,?,?)',
                         ('old', 'running', '{}', '{}', '', '2026-10-28', '2026-10-28'))
        await db.commit()
    service = ResearchService(workspace)
    await service.init()
    assert (await service.job('old'))['status'] == 'interrupted'
    assert (await workspace.lead(ident))['name'] == 'Example Property Management'
    await service.close()


def test_job_endpoints_authenticated_and_schedule_off(client):
    assert client.get('/api/research/schedule').json()['enabled'] is False
    token = client.headers.pop('Authorization')
    assert client.get('/api/research/jobs').status_code == 401
    assert client.post('/api/research/jobs', json={}).status_code == 401
    client.headers['Authorization'] = token
    assert client.post('/api/research/jobs', json={'provider': 'secret_scraper'}).status_code == 422
    assert client.put('/api/research/schedule', json={'enabled': True, 'interval_minutes': 1}).status_code == 422
    assert client.post('/api/send').status_code == 404


def test_insufficient_codex_evidence_has_actionable_state_not_422(client):
    ident = add(client)
    response = client.post(f'/api/leads/{ident}/qualify')
    assert response.status_code == 200
    assert response.json()['research_status'] == 'research_incomplete'
    assert 'no Codex usage' in response.json()['action_result']['reason']


def test_identity_does_not_accept_generic_category_alone():
    assert not identity_matches('Example Property Management', 'We do property management in Calgary')
    assert identity_matches('Example Property Management', 'Example manages Calgary property rentals')


@pytest.mark.asyncio
async def test_osm_429_never_rotates_mirrors(workspace, monkeypatch):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={'Retry-After': '600'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr('harvey.research.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ResearchError, match='429') as caught:
        await workspace.researcher.osm(BatchRequest(provider='osm'))
    assert caught.value.retryable
    assert calls == ['https://overpass-api.de/api/interpreter']


@pytest.mark.asyncio
async def test_osm_has_real_sources_dedup_and_no_inferred_city(workspace, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={'elements': [
            {'type': 'node', 'id': 1, 'tags': {'name': 'Example Property Management', 'office': 'property_management',
                                             'website': 'https://example.org'}},
            {'type': 'node', 'id': 2, 'tags': {'name': 'Example Property Management', 'office': 'property_management',
                                             'website': 'https://example.org'}}]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr('harvey.research.httpx.AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    ids = await workspace.researcher.osm(BatchRequest(provider='osm'))
    assert len(ids) == 1
    row = await workspace.lead(ids[0])
    assert row['location'] == '' and row['source_url'] == 'https://www.openstreetmap.org/node/1'
    assert not row['qualified']


@pytest.mark.asyncio
async def test_serper_discovery_dedups_and_filters_directory(workspace, monkeypatch):
    async def search(query):
        return [{'title': 'Example Property Management', 'link': 'https://example.org'},
                {'title': 'Example Property Management | Contact', 'link': 'https://example.org/contact'},
                {'title': 'Listings', 'link': 'https://yelp.com/biz/example'}]
    monkeypatch.setattr(workspace.researcher, 'search', search)
    service = ResearchService(workspace)
    ids = await service.discover(BatchRequest(provider='serper'))
    assert len(ids) == 1 and len(await workspace.rows()) == 1
    assert not (await workspace.lead(ids[0]))['qualified']


@pytest.mark.asyncio
async def test_discovery_error_persists_actionable_job(workspace, monkeypatch):
    async def unavailable(options):
        raise ResearchError('HTTP 429: retry later per provider guidance', True)
    monkeypatch.setattr(workspace.researcher, 'osm', unavailable)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(provider='osm'))
    await service.task
    row = await service.job(job['id'])
    assert row['status'] == 'failed' and row['summary']['retryable'] == 1
    assert '429' in row['message']


@pytest.mark.asyncio
async def test_paid_timeout_not_replayed(workspace):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('response lost', request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(ResearchError, match='ReadTimeout'):
            await workspace.researcher.request(c, 'POST', 'https://google.serper.dev/search', paid=True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unexpected_per_lead_error_does_not_stop_next_lead(workspace, monkeypatch):
    bad = await lead(workspace, name='Broken Lead', website='https://broken.example')
    good = await lead(workspace)
    async def verify(ident):
        if ident == bad:
            raise RuntimeError('internal test failure')
        await workspace.save(ident, verification='website_checked', evidence='Example property management in Calgary. '*8)
        return await workspace.lead(ident)
    monkeypatch.setattr(workspace.researcher, 'verify', verify)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(use_codex=False))
    await service.task
    result = await service.job(job['id'])
    assert result['status'] == 'completed' and result['summary']['processed'] == 2
    assert result['summary']['drafts'] == 1 and result['summary']['failed'] == 1


@pytest.mark.asyncio
async def test_dnc_during_source_fetch_stops_draft(workspace, monkeypatch):
    ident = await lead(workspace)
    async def verify(ident):
        await workspace.save(ident, verification='website_checked', evidence='Example property management in Calgary. '*8,
                             stage='do_not_contact', casl_basis='do_not_contact')
        return await workspace.lead(ident)
    monkeypatch.setattr(workspace.researcher, 'verify', verify)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(use_codex=False))
    await service.task
    assert (await service.job(job['id']))['items'][0]['status'] == 'do_not_contact'
    assert not (await workspace.lead(ident))['draft']


@pytest.mark.asyncio
async def test_prior_approval_and_evidence_survive_schema_upgrade(workspace):
    ident = await lead(workspace)
    await workspace.save(ident, stage='approved_for_manual_contact', casl_basis='express_consent',
                         casl_evidence='Documented test consent', notes='Keep this note', draft='Existing owner-reviewed draft')
    await workspace.init()  # idempotent additive schema preparation
    row = await workspace.lead(ident)
    assert row['stage'] == 'approved_for_manual_contact'
    assert row['casl_evidence'] == 'Documented test consent'
    assert row['draft'] == 'Existing owner-reviewed draft' and row['notes'] == 'Keep this note'


def test_job_progress_and_cancel_api(client, monkeypatch):
    add(client)
    async def slow(ident):
        await asyncio.Event().wait()
    monkeypatch.setattr(client.app.state.workspace.researcher, 'verify', slow)
    response = client.post('/api/research/jobs', json={'provider': 'existing', 'use_codex': False})
    assert response.status_code == 202
    ident = response.json()['id']
    assert client.get('/api/research/jobs').json()[0]['id'] == ident
    assert client.get(f'/api/research/jobs/{ident}').status_code == 200
    response = client.post(f'/api/research/jobs/{ident}/cancel')
    assert response.status_code == 200 and response.json()['status'] == 'cancelled'
    assert client.get('/api/status').json()['outbound_email'] == 'disabled'


@pytest.mark.asyncio
async def test_codex_budget_and_prior_drafts_are_preserved(workspace, monkeypatch):
    first = await lead(workspace)
    second = await lead(workspace, name='Other Property Management', website='https://other.example')
    await workspace.save(first, draft='Owner-reviewed draft', stage='approved_for_manual_contact',
                         casl_basis='express_consent', casl_evidence='Test consent')
    async def verify(ident):
        await workspace.save(ident, verification='website_checked', evidence='Calgary property management services. '*8)
        return await workspace.lead(ident)
    calls = []
    async def review(ident, **kwargs):
        calls.append(ident)
        return await workspace.lead(ident)
    monkeypatch.setattr(workspace.researcher, 'verify', verify)
    monkeypatch.setattr(workspace, 'ai_review', review)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(max_codex_reviews=1))
    await service.task
    result = await service.job(job['id'])
    assert len(calls) == 1 and result['summary']['codex_reviews'] == 1
    assert result['summary']['drafts_retained'] == 1
    assert (await workspace.lead(first))['draft'] == 'Owner-reviewed draft'
    assert (await workspace.lead(first))['stage'] == 'approved_for_manual_contact'
    assert (await workspace.lead(second))['research_status'] == 'needs_human_review'


def test_sqlite_backup_keeps_existing_records_private(tmp_path):
    import importlib.util
    import sqlite3
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('backup_data', Path(__file__).resolve().parent.parent/'scripts/backup-data.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source, target = tmp_path/'old.db', tmp_path/'backups/copy.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE leads (name TEXT, notes TEXT)')
        db.execute('INSERT INTO leads VALUES (?,?)', ('Existing lead', 'Owner notes'))
    module.backup(source, target)
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT * FROM leads').fetchone() == ('Existing lead', 'Owner notes')
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        module.backup(source, target)


@pytest.mark.asyncio
async def test_second_process_cannot_interrupt_active_database(workspace, monkeypatch):
    await lead(workspace)
    entered = asyncio.Event()
    async def slow(ident):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(workspace.researcher, 'verify', slow)
    first = ResearchService(workspace)
    await first.init()
    try:
        job = await first.start(BatchRequest())
        await asyncio.wait_for(entered.wait(), timeout=3)
        second = ResearchService(workspace)
        with pytest.raises(RuntimeError, match='Another MOWZIVA service'):
            await second.init()
        assert (await first.job(job['id']))['status'] == 'running'
    finally:
        await first.close()


@pytest.mark.asyncio
async def test_codex_quotes_must_exist_in_official_evidence(workspace, monkeypatch):
    ident = await lead(workspace)
    evidence = 'Example provides property management in Calgary. ' * 8
    await workspace.save(ident, verification='website_checked', evidence=evidence)
    async def ready(self):
        return ''
    async def think(self, prompt, **kwargs):
        return {'fit_summary': 'Review required', 'evidence': ['Example provides property management in Calgary.',
                    'MOWZIVA has commercial insurance'], 'unknowns': ['Service scope']}
    monkeypatch.setattr('harvey.brain.Brain.preflight', ready)
    monkeypatch.setattr('harvey.brain.Brain.think_json', think)
    row = await workspace.ai_review(ident)
    result = json.loads(row['ai_review'])
    assert result['evidence'] == ['Example provides property management in Calgary.']


@pytest.mark.asyncio
async def test_existing_batch_respects_categories_and_paid_resolution_opt_in(workspace):
    assert not BatchRequest().resolve_websites
    target = await lead(workspace)
    await workspace.add(LeadInput(name='Example Clinic', website='https://clinic.example',
                                  source_url='https://clinic.example', industry='clinic', location='Calgary'))
    service = ResearchService(workspace)
    assert await service.discover(BatchRequest()) == [target]


@pytest.mark.asyncio
async def test_robots_redirect_preserves_rules_and_private_target_rejected(workspace):
    seen = []
    def handler(request):
        seen.append(request.url.path)
        if request.url.path == '/robots.txt':
            return httpx.Response(301, headers={'Location': '/actual-robots.txt'})
        return httpx.Response(200, text='User-agent: *\nDisallow: /private')
    workspace.researcher.factory = source_factory(handler)
    with pytest.raises(ResearchError, match='robots.txt disallows'):
        await workspace.researcher.page('https://example.org/private')
    assert seen == ['/robots.txt', '/actual-robots.txt']
    def private(request):
        return httpx.Response(301, headers={'Location': 'http://127.0.0.1/robots.txt'})
    workspace.researcher.factory = source_factory(private)
    with pytest.raises(ResearchError, match='invalid or unsafe'):
        await workspace.researcher.page('https://another.example/private')


@pytest.mark.asyncio
async def test_robots_request_rate_directive_is_honored(workspace):
    def handler(request):
        return httpx.Response(200, text='User-agent: *\nRequest-rate: 1/10')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await workspace.researcher.robots(c, 'https://example.org/public')
    assert workspace.researcher.next_host['example.org'] >= time.monotonic()+9


@pytest.mark.asyncio
async def test_partial_contact_failure_remains_visible_in_completed_job(workspace):
    ident = await lead(workspace)
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        if request.url.path == '/contact':
            return httpx.Response(429, headers={'Retry-After': '600'})
        return httpx.Response(200, headers={'content-type': 'text/html'}, text='<h1>Example Property Management</h1>'
            '<p>Example provides property management services in Calgary, Alberta, for rental property owners.</p>'
            '<a href="/contact">Contact us</a>')
    workspace.researcher.factory = source_factory(handler)
    service = ResearchService(workspace)
    job = await service.start(BatchRequest(use_codex=False))
    await service.task
    result = await service.job(job['id'])
    assert result['status'] == 'completed' and result['summary']['verified'] == 1
    assert result['summary']['retryable'] == 1 and result['summary']['failed'] == 1
    assert result['items'][0]['status'] == 'needs_human_review'
    assert '429' in result['items'][0]['message']
    assert (await workspace.lead(ident))['sources']


def test_verified_calgary_page_can_supply_missing_listing_geography():
    from harvey.mowziva import qualify
    row = {'location': '', 'industry': 'property management', 'verification': 'website_checked',
           'evidence': 'Example Property Management provides services in Calgary, Alberta.'}
    score, reasons = qualify(row)
    assert score == 75 and any('Calgary' in reason for reason in reasons)
    row['verification'] = 'unverified'
    assert qualify(row)[0] == 35  # listing/search assumptions alone never count as checked evidence


@pytest.mark.asyncio
async def test_social_directory_is_not_treated_as_official_business_site(workspace, monkeypatch):
    ident = await lead(workspace, website='https://linkedin.com/company/example')
    called = []
    async def page(url):
        called.append(url)
        raise AssertionError('No social profile should be fetched as an official website')
    monkeypatch.setattr(workspace.researcher, 'page', page)
    with pytest.raises(ResearchError, match='No official website recorded'):
        await workspace.researcher.verify(ident)
    assert called == []


def test_manual_verify_does_not_inherit_a_paid_batch_budget(client, monkeypatch):
    ident = add(client, website='')
    client.app.state.workspace.researcher.options = BatchRequest(resolve_websites=True)
    calls = []
    async def resolve(row):
        calls.append(row)
        raise AssertionError('Manual Verify must not silently spend a prior batch budget')
    monkeypatch.setattr(client.app.state.workspace.researcher, 'resolve', resolve)
    response = client.post(f'/api/leads/{ident}/verify')
    assert response.status_code == 502 and 'does not spend search credits' in response.json()['detail']
    assert calls == []
