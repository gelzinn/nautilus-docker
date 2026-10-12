from types import SimpleNamespace
import unittest
from nautilus_docker.sorting import natural_key, size_bytes, creation_time, sort_value


class SortingTests(unittest.TestCase):
    def test_names_sort_naturally_and_ignore_case(self):
        self.assertEqual(sorted(['container10', 'Container2', 'container1'], key=natural_key),
                         ['container1', 'Container2', 'container10'])

    def test_size_sort_compares_bytes_across_units(self):
        self.assertLess(size_bytes('950 MB'), size_bytes('1.2GB'))
        self.assertEqual(size_bytes('1KiB'), 1024)
        self.assertEqual(size_bytes('1kB'), 1000)
        self.assertEqual(size_bytes('—'), -1)

    def test_creation_sort_accounts_for_timezones(self):
        self.assertEqual(creation_time('2024-01-01 12:00:00 +0000 UTC'),
                         creation_time('2024-01-01 09:00:00 -0300 ZONE'))
        self.assertLess(creation_time('2024-01-01 12:00:00 +0000 UTC'),
                        creation_time('2024-02-01 12:00:00 +0000 UTC'))

    def test_created_column_uses_date_instead_of_relative_caption(self):
        a = SimpleNamespace(data={'CreatedSince': '2 days ago', 'CreatedAt': '2024-01-01 12:00:00 +0000 UTC'})
        b = SimpleNamespace(data={'CreatedSince': '1 day ago', 'CreatedAt': '2024-01-02 12:00:00 +0000 UTC'})
        self.assertLess(sort_value(a, 'CreatedSince'), sort_value(b, 'CreatedSince'))
