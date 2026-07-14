"""Format codecs: one module per fixture dialect.

Each codec exposes the same three functions:

``detect(text, data)``
    Return the concrete format id (e.g. ``"vcr-ruby"``) if the input looks
    like this format, else ``None``. ``data`` is the pre-parsed JSON value
    when the input is valid JSON, otherwise ``None``.

``load(text, notes, base_url=None)``
    Parse the input into a :class:`~fixmux.model.Fixture`. Lossy or
    best-effort decisions append a human-readable string to ``notes``.

``dump(fixture, notes, strict=False, **options)``
    Serialize a fixture into this format. In strict mode, anything the
    format cannot represent raises
    :class:`~fixmux.errors.UnsupportedFeatureError`; otherwise a note is
    appended and a best-effort output is produced.
"""

from . import har, nock, vcr, wiremock  # noqa: F401
