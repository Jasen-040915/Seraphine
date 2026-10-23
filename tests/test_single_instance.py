import sys
import unittest
import uuid
import os
from unittest.mock import patch
from app.common.single_instance import SingleInstance


@unittest.skipUnless(sys.platform == 'win32', 'Windows named objects')
class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.name = 'Local\\Seraphine-Test-' + uuid.uuid4().hex
        self.handles = []

    def open(self):
        instance = SingleInstance(self.name)
        self.handles.append(instance)
        return instance

    def tearDown(self):
        for instance in self.handles:
            instance.close()

    def test_second_instance_only_wakes_first_and_does_not_own_input(self):
        first, second = self.open(), self.open()
        self.assertTrue(first.acquired)
        self.assertFalse(second.acquired)
        self.assertTrue(second.request_activation())
        self.assertTrue(first.consume_activation())
        self.assertFalse(first.consume_activation())

    def test_closing_secondary_does_not_release_primary_lock(self):
        first, second = self.open(), self.open()
        second.close()
        third = self.open()
        self.assertFalse(third.acquired)
        self.assertTrue(first.acquired)

    def test_exit_releases_lock_for_next_launch(self):
        first = self.open()
        first.close(); first.close()
        self.assertTrue(self.open().acquired)

    def test_secondary_reads_owner_pid_and_does_not_overwrite_it(self):
        first, second = self.open(), self.open()
        self.assertEqual(first.owner_pid, os.getpid())
        self.assertEqual(second.owner_pid, first.owner_pid)
        second.close()
        self.assertEqual(first.owner_pid, os.getpid())

    def test_foreground_permission_is_transferred_to_owner_before_wake(self):
        first, second = self.open(), self.open()
        first.owner[:] = (12345).to_bytes(4, 'little')
        with patch('app.common.single_instance.allow_foreground_for') as allow:
            self.assertTrue(second.request_activation())
            allow.assert_called_once_with(12345)
        self.assertTrue(first.consume_activation())
