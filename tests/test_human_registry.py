"""Registration checks only. No real OAuth/user authentication is claimed."""
import unittest
from broker_lab.core import Denied
from broker_lab.human_registry import HumanRegistry

class IntendedHumanRegistryTests(unittest.TestCase):
    def test_exact_preverified_subject_maps_to_intended_delegation(self):
        registry=HumanRegistry()
        self.assertEqual(registry.for_verified_subject('00D000000000001AAA','005000000000001AAA').intended_records,frozenset({'A'}))
        self.assertEqual(registry.for_verified_subject('00D000000000001AAA','005000000000002AAA').intended_records,frozenset({'B'}))
    def test_customer_label_email_runtime_and_other_org_are_not_human_login(self):
        registry=HumanRegistry()
        for user in ('CustomerA','customer-a@example.invalid','005000000000003AAA','unknown'):
            with self.assertRaises(Denied):registry.for_verified_subject('00D000000000001AAA',user)
        with self.assertRaises(Denied):registry.for_verified_subject('different-org','005000000000001AAA')
