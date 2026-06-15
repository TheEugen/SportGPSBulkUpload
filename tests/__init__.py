"""Test suite for komoot_bulk_upload (stdlib unittest; no extra dependencies).

Run from the project root with:

    py -m unittest discover -s tests

The tests are self-contained — they synthesize their own GPX/TCX/state files in
temp dirs and mock komoot's network with a fake requests session, so no komoot
credentials, network access, or files under examples/ are needed.
"""
