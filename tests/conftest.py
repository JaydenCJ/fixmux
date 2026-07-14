"""Shared fixtures: realistic sample captures in all four dialects.

The samples describe the *same* two exchanges — a JSON GET and a POST with
a JSON request body — expressed the way each ecosystem's recorder actually
writes them (vcrpy's sorted-key YAML, Ruby VCR's ``---`` document and
RFC 2822 timestamps, DevTools-style HAR, nock's parsed-JSON responses).
Cross-format tests convert between them and assert semantic equivalence.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def har_text() -> str:
    return json.dumps(
        {
            "log": {
                "version": "1.2",
                "creator": {"name": "WebInspector", "version": "537.36"},
                "entries": [
                    {
                        "startedDateTime": "2026-03-01T09:30:00.000Z",
                        "time": 42.5,
                        "request": {
                            "method": "GET",
                            "url": "http://example.test/api/members?page=2",
                            "httpVersion": "HTTP/1.1",
                            "cookies": [],
                            "headers": [
                                {"name": "Accept", "value": "application/json"},
                                {"name": "X-Trace", "value": "one"},
                                {"name": "X-Trace", "value": "two"},
                            ],
                            "queryString": [{"name": "page", "value": "2"}],
                            "headersSize": -1,
                            "bodySize": 0,
                        },
                        "response": {
                            "status": 200,
                            "statusText": "OK",
                            "httpVersion": "HTTP/1.1",
                            "cookies": [],
                            "headers": [
                                {"name": "Content-Type", "value": "application/json"}
                            ],
                            "content": {
                                "size": 27,
                                "mimeType": "application/json",
                                "text": '{"members": ["aya", "ben"]}',
                            },
                            "redirectURL": "",
                            "headersSize": -1,
                            "bodySize": 27,
                        },
                        "cache": {},
                        "timings": {"send": 1, "wait": 40, "receive": 1.5},
                    },
                    {
                        "startedDateTime": "2026-03-01T09:30:01.000Z",
                        "time": 18.0,
                        "request": {
                            "method": "POST",
                            "url": "http://example.test/api/members",
                            "httpVersion": "HTTP/1.1",
                            "cookies": [],
                            "headers": [
                                {"name": "Content-Type", "value": "application/json"}
                            ],
                            "queryString": [],
                            "postData": {
                                "mimeType": "application/json",
                                "text": '{"name": "chika"}',
                            },
                            "headersSize": -1,
                            "bodySize": 17,
                        },
                        "response": {
                            "status": 201,
                            "statusText": "Created",
                            "httpVersion": "HTTP/1.1",
                            "cookies": [],
                            "headers": [
                                {"name": "Content-Type", "value": "application/json"},
                                {"name": "Location", "value": "/api/members/3"},
                            ],
                            "content": {
                                "size": 9,
                                "mimeType": "application/json",
                                "text": '{"id": 3}',
                            },
                            "redirectURL": "",
                            "headersSize": -1,
                            "bodySize": 9,
                        },
                        "cache": {},
                        "timings": {"send": 1, "wait": 16, "receive": 1},
                    },
                ],
            }
        }
    )


@pytest.fixture
def vcrpy_yaml_text() -> str:
    return """interactions:
- request:
    body: null
    headers:
      Accept:
      - application/json
      X-Trace:
      - one
      - two
    method: GET
    uri: http://example.test/api/members?page=2
  response:
    body:
      string: '{"members": ["aya", "ben"]}'
    headers:
      Content-Type:
      - application/json
    status:
      code: 200
      message: OK
- request:
    body: '{"name": "chika"}'
    headers:
      Content-Type:
      - application/json
    method: POST
    uri: http://example.test/api/members
  response:
    body:
      string: '{"id": 3}'
    headers:
      Content-Type:
      - application/json
      Location:
      - /api/members/3
    status:
      code: 201
      message: Created
version: 1
"""


@pytest.fixture
def ruby_vcr_text() -> str:
    return """---
http_interactions:
- request:
    method: get
    uri: http://example.test/api/members?page=2
    body:
      encoding: UTF-8
      string: ''
    headers:
      Accept:
      - application/json
      X-Trace:
      - one
      - two
  response:
    status:
      code: 200
      message: OK
    headers:
      Content-Type:
      - application/json
    body:
      encoding: UTF-8
      string: '{"members": ["aya", "ben"]}'
  recorded_at: Sun, 01 Mar 2026 09:30:00 GMT
- request:
    method: post
    uri: http://example.test/api/members
    body:
      encoding: UTF-8
      string: '{"name": "chika"}'
    headers:
      Content-Type:
      - application/json
  response:
    status:
      code: 201
      message: Created
    headers:
      Content-Type:
      - application/json
      Location:
      - /api/members/3
    body:
      encoding: UTF-8
      string: '{"id": 3}'
  recorded_at: Sun, 01 Mar 2026 09:30:01 GMT
recorded_with: VCR 6.2.0
"""


@pytest.fixture
def nock_text() -> str:
    return json.dumps(
        [
            {
                "scope": "http://example.test:80",
                "method": "GET",
                "path": "/api/members?page=2",
                "body": "",
                "status": 200,
                "response": {"members": ["aya", "ben"]},
                "reqheaders": {"Accept": "application/json", "X-Trace": ["one", "two"]},
                "rawHeaders": ["Content-Type", "application/json"],
            },
            {
                "scope": "http://example.test:80",
                "method": "POST",
                "path": "/api/members",
                "body": {"name": "chika"},
                "status": 201,
                "response": {"id": 3},
                "reqheaders": {"Content-Type": "application/json"},
                "rawHeaders": [
                    "Content-Type",
                    "application/json",
                    "Location",
                    "/api/members/3",
                ],
            },
        ]
    )


@pytest.fixture
def wiremock_text() -> str:
    return json.dumps(
        {
            "mappings": [
                {
                    "request": {
                        "method": "GET",
                        "url": "/api/members?page=2",
                        "headers": {
                            "Accept": {"equalTo": "application/json"},
                            "X-Trace": {"equalTo": "one, two"},
                        },
                    },
                    "response": {
                        "status": 200,
                        "headers": {"Content-Type": "application/json"},
                        "jsonBody": {"members": ["aya", "ben"]},
                    },
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "/api/members",
                        "headers": {"Content-Type": {"equalTo": "application/json"}},
                        "bodyPatterns": [{"equalTo": '{"name": "chika"}'}],
                    },
                    "response": {
                        "status": 201,
                        "statusMessage": "Created",
                        "headers": {
                            "Content-Type": "application/json",
                            "Location": "/api/members/3",
                        },
                        "body": '{"id": 3}',
                    },
                },
            ]
        }
    )
