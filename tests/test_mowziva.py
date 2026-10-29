"""MOWZIVA functional workflow, no real network, model calls or email."""
import csv
import io
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from harvey.config import load_config
from harvey.mowziva import create_app, csv_text, qualify


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / 'leads.db'), tmp_path / 'token')
    with TestClient(app, base_url='http://127.0.0.1:8765') as client:
        client.headers['Authorization'] = 'Bearer ' + app.state.token_store.get()
        app.state.workspace.researcher.delay = 0
        app.state.workspace.researcher.retry_base = 0
        yield client


def add(client, **overrides):
    lead = {'name': 'Example Property Management', 'website': 'https://example.org',
            'source_url': 'https://example.org/contact', 'location': 'Calgary, Cranston SE Alberta',
            'industry': 'property management', 'phone': '403-555-0100', **overrides}
    response = client.post('/api/leads', json=lead)
    assert response.status_code == 200, response.text
    return response.json()['id']


def fake_site(monkeypatch, text=None, location='Calgary'):
    text = text or (f'<html><body><h1>Example Property Management</h1><p>We provide property '
                    f'management in {location}, Alberta. Our team manages rental properties and '
                    'condominiums.</p><a href="/contact">Contact us</a>'
                    '<a href="/vendors">Vendor registration</a></body></html>')
    async def handler(request):
        return httpx.Response(200, headers={'content-type': 'text/html'}, text=text)
    @asynccontextmanager
    async def factory(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
            yield c
    monkeypatch.setattr('harvey.mowziva.public_http_client', factory)


def test_codex_and_disabled_channels():
    config = load_config()
    assert config.brain.provider == 'codex'
    assert not config.channels.email.enabled
    assert config.channels.email.max_daily_sends == 0
    assert config.channels.email.require_approval
    assert not config.channels.linkedin.enabled
    assert config.usage.quiet_hours.timezone == 'America/Edmonton'


def test_dashboard_is_locked_and_no_send_route(client):
    token = client.headers.pop('Authorization')
    assert client.get('/').status_code == 200
    assert 'OUTBOUND EMAIL DISABLED' in client.get('/').text
    assert client.get('/api/leads').status_code == 401
    client.headers['Authorization'] = token
    assert client.post('/api/send').status_code == 404
    assert client.post('/api/leads/anything/send').status_code == 404
    assert client.post('/api/sending/resume').status_code == 404
    assert client.get('/api/status').json()['outbound_email'] == 'disabled'


def test_cross_origin_mutation_denied(client):
    r = client.post('/api/leads', json={}, headers={'Origin': 'https://attacker.example'})
    assert r.status_code == 403


def test_add_dedup_search_and_persist(client):
    ident = add(client)
    assert add(client) == ident
    assert len(client.get('/api/leads').json()) == 1
    assert client.get('/api/leads?search=Cranston').json()[0]['id'] == ident
    assert client.get('/api/leads?search=Edmonton').json() == []
    r = client.patch(f'/api/leads/{ident}', json={'stage': 'follow_up', 'follow_up_on': '2026-11-03',
                                                'notes': 'Check vendor requirements'})
    assert r.status_code == 200
    assert client.get('/api/leads?stage=follow_up').json()[0]['follow_up_on'] == '2026-11-03'


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://127.0.0.1',
                                'http://169.254.169.254/latest', 'https://a:b@example.org'])
def test_unsafe_source_rejected(client, url):
    assert client.post('/api/leads', json={'name': 'No', 'source_url': url}).status_code == 422


def test_no_unverified_qualified_leads_or_drafts(client):
    ident = add(client)
    assert client.get('/api/status').json()['qualified'] == 0
    assert client.patch(f'/api/leads/{ident}', json={'stage': 'qualified'}).status_code == 409
    assert client.post(f'/api/leads/{ident}/draft').status_code == 422
    response = client.post(f'/api/leads/{ident}/qualify')
    assert response.status_code == 200
    assert response.json()['action_result']['status'] == 'research_incomplete'
    assert 'no Codex usage' in response.json()['action_result']['reason']


def test_verification_draft_casl_approval_and_export(client, monkeypatch):
    fake_site(monkeypatch)
    ident = add(client)
    row = client.post(f'/api/leads/{ident}/verify').json()
    assert row['verification'] == 'website_checked'
    assert row['qualified'] is True
    assert row['vendor_url'] == 'https://example.org/vendors'
    assert row['contact_url'] == 'https://example.org/contact'
    assert client.get('/api/status').json()['qualified'] == 1
    assert client.post(f'/api/leads/{ident}/draft').status_code == 200
    row = client.get(f'/api/leads/{ident}').json()
    assert 'DRAFT ONLY' in row['draft'] and 'capacity assessment' in row['draft']
    assert 'insurance' not in row['draft'].lower()
    r = client.patch(f'/api/leads/{ident}', json={'stage': 'approved_for_manual_contact'})
    assert r.status_code == 409
    r = client.patch(f'/api/leads/{ident}', json={'stage': 'approved_for_manual_contact',
        'casl_basis': 'express_consent', 'casl_evidence': 'Test record only: consent on 2026-10-26'})
    assert r.status_code == 200
    assert client.get('/api/status').json()['outbound_email'] == 'disabled'
    exported = list(csv.DictReader(io.StringIO(client.get('/api/leads.csv').text)))
    assert exported[0]['name'] == 'Example Property Management'
    assert exported[0]['stage'] == 'approved_for_manual_contact'


def test_search_location_does_not_prove_calgary(client, monkeypatch):
    fake_site(monkeypatch, location='Edmonton')
    ident = add(client)
    row = client.post(f'/api/leads/{ident}/verify').json()
    assert row['verification'] == 'website_checked'
    assert not row['qualified']
    assert client.get('/api/status').json()['qualified'] == 0


def test_category_requires_page_evidence(client, monkeypatch):
    fake_site(monkeypatch, text='<h1>Example Property Management</h1><p>Welcome to this Calgary site. '
              'This domain is for sale. There is no active business at this location.</p>')
    ident = add(client, industry='clinic')
    row = client.post(f'/api/leads/{ident}/verify').json()
    assert not row['qualified']


def test_verification_failure_not_success(client, monkeypatch):
    @asynccontextmanager
    async def broken(**kwargs):
        raise httpx.ConnectError('blocked')
        yield
    monkeypatch.setattr('harvey.mowziva.public_http_client', broken)
    ident = add(client)
    assert client.post(f'/api/leads/{ident}/verify').status_code == 502
    assert client.get(f'/api/leads/{ident}').json()['verification'] == 'verification_failed'


def test_suppression_excludes_csv_and_drafts(client):
    ident = add(client)
    assert client.patch(f'/api/leads/{ident}', json={'stage': 'do_not_contact'}).status_code == 200
    assert client.post(f'/api/leads/{ident}/draft').status_code == 409
    assert list(csv.DictReader(io.StringIO(client.get('/api/leads.csv').text))) == []
    assert client.get('/api/leads').json()[0]['casl_basis'] == 'do_not_contact'


def test_csv_formula_injection_and_quoting():
    text = csv_text([{'name': '=HYPERLINK("bad")', 'phone': '+14035550100',
                      'notes': ' \t@SUM(1,2)\nsecond line'}])
    row = list(csv.DictReader(io.StringIO(text)))[0]
    assert row['name'].startswith("'=")
    assert row['phone'].startswith("'+")
    assert row['notes'].startswith("' \t@")


def test_discovery_uses_bounded_provider_with_budget(client, monkeypatch):
    seen = {}
    async def osm(self, options):
        seen['options'] = options
        assert options.provider == 'osm'
        assert options.max_estimated_cost == 0.01
        assert options.max_search_calls == 1
        return []
    monkeypatch.setattr('harvey.research.SourceResearch.osm', osm)
    r = client.post('/api/discover', json={'segment': 'condominium management'})
    assert r.status_code == 200 and r.json()['queries'] == 1
    assert seen['options'].segments == ['condominium management']
    assert client.post('/api/discover', json={'provider': 'unknown'}).status_code == 422
    assert client.post('/api/discover', json={'limit': 51}).status_code == 422


def test_codex_unavailable_does_not_fallback(client, monkeypatch):
    fake_site(monkeypatch)
    ident = add(client)
    assert client.post(f'/api/leads/{ident}/verify').status_code == 200
    async def unavailable(self):
        assert self.provider == 'codex'
        return 'Codex login requires renewal'
    monkeypatch.setattr('harvey.brain.Brain.preflight', unavailable)
    r = client.post(f'/api/leads/{ident}/qualify')
    assert r.status_code == 503 and 'renewal' in r.json()['detail']
    assert client.get(f'/api/leads/{ident}').json()['ai_review'] == ''


def test_codex_review_cannot_change_score_or_stage(client, monkeypatch):
    fake_site(monkeypatch)
    ident = add(client)
    row = client.post(f'/api/leads/{ident}/verify').json()
    async def ready(self):
        return ''
    async def think(self, prompt, **kwargs):
        assert 'UNTRUSTED' in prompt and self.provider == 'codex'
        return {'fit_summary': 'Evidence supports further research', 'evidence': [], 'score': 100,
                'stage': 'approved_for_manual_contact', 'unknowns': ['property count']}
    monkeypatch.setattr('harvey.brain.Brain.preflight', ready)
    monkeypatch.setattr('harvey.brain.Brain.think_json', think)
    reviewed = client.post(f'/api/leads/{ident}/qualify').json()
    assert reviewed['score'] == row['score'] and reviewed['stage'] == 'new'
    assert reviewed['casl_basis'] == 'unconfirmed'
