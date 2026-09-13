"""Pricing contracts without contacting a payment provider."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.parse import parse_qs

from quantify_app import billing
from quantify_app.database import connect, initialize


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "pricing.sqlite3"
        initialize(self.db)
        self.conn = connect(self.db)
        self.conn.execute("INSERT INTO organizations(id,name,created_at) VALUES('org-price','Price test','2026-09-13')")
        self.conn.commit()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.conn.close()
        self.env.stop()
        self.tmp.cleanup()

    def add_location(self, name):
        self.conn.execute("""INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
            latitude,longitude,timezone,open_hour,close_hour) VALUES(?,'org-price',?,'Cafe','','Denver','CO','',0,0,'America/Denver',7,21)""", (name, name))
        self.conn.commit()

    def test_new_workspaces_start_with_one_location_and_legacy_records_stay(self):
        self.assertEqual(billing.ensure_subscription(self.conn, 'org-price')['plan'], 'solo')
        self.assertEqual([(p['code'], p['monthly']) for p in billing.public_catalog()], [('solo', 39), ('team', 99)])
        self.assertTrue(all(not p['checkout_ready'] for p in billing.public_catalog()))
        for code, price in [('standard', 79), ('founding', 49)]:
            self.conn.execute("UPDATE subscriptions SET plan=? WHERE organization_id='org-price'", (code,))
            self.conn.commit()
            overview = billing.overview(self.conn, 'org-price')
            self.assertEqual(overview['plan']['monthly'], price)
            self.assertIsNone(overview['location_allowance']['limit'])
        self.conn.execute("DELETE FROM subscriptions WHERE organization_id='org-price'")
        self.conn.commit()
        self.add_location('old-location')
        self.assertEqual(billing.ensure_subscription(self.conn, 'org-price')['plan'], 'standard')

    def test_trial_choice_changes_allowance_without_payment_or_dropping_locations(self):
        original = billing.ensure_subscription(self.conn, 'org-price')
        self.add_location('one')
        with self.assertRaises(ValueError):
            billing.require_location_capacity(self.conn, 'org-price')
        self.conn.rollback()
        result = billing.change_plan(self.conn, 'org-price', 'team')
        self.assertEqual(result['status'], 'trialing')
        self.assertEqual(billing.ensure_subscription(self.conn, 'org-price')['trial_end'], original['trial_end'])
        self.add_location('two')
        self.add_location('three')
        with self.assertRaises(ValueError):
            billing.change_plan(self.conn, 'org-price', 'solo')
        self.conn.rollback()
        self.assertEqual(billing.location_allowance(self.conn, 'org-price')['used'], 3)
        self.assertEqual(billing.ensure_subscription(self.conn, 'org-price')['plan'], 'team')

    def test_checkout_is_unavailable_without_exact_price_and_never_activates_locally(self):
        billing.ensure_subscription(self.conn, 'org-price')
        with self.assertRaises(RuntimeError):
            billing.start_checkout(self.conn, 'org-price', 'a@example.test', 'A', 'solo', 'https://example.test/')
        with patch.dict(os.environ, {'STRIPE_SECRET_KEY': 'sk_test_example', 'STRIPE_PRICE_ID_SOLO': 'price_solo'}):
            with patch.object(billing, '_request', return_value={'active': True, 'currency': 'usd', 'unit_amount': 7900, 'recurring': {'interval': 'month'}}) as request:
                with self.assertRaises(RuntimeError):
                    billing.start_checkout(self.conn, 'org-price', 'a@example.test', 'A', 'solo', 'https://example.test/')
                self.assertEqual(request.call_count, 1)
            self.conn.execute("UPDATE subscriptions SET provider_customer_id='cus_a' WHERE organization_id='org-price'")
            self.conn.commit()
            with patch.object(billing, '_request', side_effect=[{'active': True, 'currency': 'usd', 'unit_amount': 3900, 'recurring': {'interval': 'month'}}, {'url': 'https://checkout.example.test/session'}]):
                result = billing.start_checkout(self.conn, 'org-price', 'a@example.test', 'A', 'solo', 'https://example.test/')
            self.assertTrue(result['pending'])
            self.assertEqual(billing.ensure_subscription(self.conn, 'org-price')['status'], 'trialing')

    def test_paid_accounts_cannot_switch_locally_or_create_duplicate_subscription(self):
        billing.ensure_subscription(self.conn, 'org-price')
        self.conn.execute("UPDATE subscriptions SET provider_subscription_id='sub_a',status='active' WHERE organization_id='org-price'")
        self.conn.commit()
        with self.assertRaises(ValueError):
            billing.change_plan(self.conn, 'org-price', 'team')
        with self.assertRaises(ValueError):
            billing.start_checkout(self.conn, 'org-price', 'a@example.test', 'A', 'team', 'https://example.test/')

    def test_confirmed_provider_price_updates_plan_and_unknown_prices_preserve_it(self):
        billing.ensure_subscription(self.conn, 'org-price')
        self.conn.execute("UPDATE subscriptions SET provider_customer_id='cus_a' WHERE organization_id='org-price'")
        self.conn.commit()
        event = {'type': 'customer.subscription.updated', 'data': {'object': {'customer': 'cus_a', 'id': 'sub_a', 'status': 'active', 'items': {'data': [{'price': {'id': 'price_team'}}]}}}}
        with patch.dict(os.environ, {'STRIPE_PRICE_ID_TEAM': 'price_team'}):
            billing.apply_stripe_event(self.conn, event)
            self.assertEqual(billing.ensure_subscription(self.conn, 'org-price')['plan'], 'team')
            event['data']['object']['items']['data'][0]['price']['id'] = 'unknown_price'
            billing.apply_stripe_event(self.conn, event)
            self.assertEqual(billing.ensure_subscription(self.conn, 'org-price')['plan'], 'team')

    def test_nested_checkout_metadata_is_encoded_as_provider_fields(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with patch.dict(os.environ, {'STRIPE_SECRET_KEY': 'sk_test_example'}), patch.object(billing.urllib.request, 'urlopen', return_value=response) as request:
            billing._request('/checkout/sessions', {'subscription_data': {'metadata': {'plan': 'team'}}, 'line_items': [{'price': 'price_team', 'quantity': 1}]})
        body = parse_qs(request.call_args.args[0].data.decode())
        self.assertEqual(body['subscription_data[metadata][plan]'], ['team'])
        self.assertEqual(body['line_items[0][quantity]'], ['1'])
