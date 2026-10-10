"""
Unit-test fixtures: no QuestDB server is needed for anything in this directory.

The QuestDB SDK connects (and fetches server settings) when a Sender is entered, so replace
the Sender class used by the writer with a mock. Integration and simulation tests use the
real SDK against a running QuestDB.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def mock_sender(monkeypatch):
    sender_cls = MagicMock(name="Sender")
    monkeypatch.setattr("questdb_writer.Sender", sender_cls)
    return sender_cls
