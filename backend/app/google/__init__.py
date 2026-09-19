"""Google integration boundary (A02).

All Google-specific client objects, transport details and raw response shapes
stay inside this package. API routes consume only normalized EVA contracts
produced here. Tokens and secrets never leave: error types carry sanitized
messages, and stored credentials live outside source control under the
configured ``GOOGLE_CREDENTIALS_PATH`` (gitignored).
"""
