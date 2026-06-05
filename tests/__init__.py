"""Unit tests for sub-account topology helpers.

These tests intentionally stay at unit level — they mock the
``control.repo`` layer so they don't need a real Postgres. Integration
behaviour (real Binance API calls, real DB) is covered manually with the
onboarding wizard and the smoke checklist in
``docs/subaccount-topology.md``.
"""
