"""Public HTTPS trust data must survive the release privacy filter."""
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from unittest.mock import patch

from scripts.package_portable import collect, omit


class ReleasePackagingTests(unittest.TestCase):
    def test_runtime_ca_survives_but_other_pem_and_private_paths_do_not(self):
        self.assertFalse(omit(PurePosixPath('certifi/cacert.pem')))
        self.assertFalse(omit(PurePosixPath('CERTIFI/CACERT.PEM')))
        for name in ('private.pem', 'certifi/private.pem', 'secrets/certifi/cacert.pem',
                     'state/certifi/cacert.pem', 'nested/certifi/cacert.pem',
                     '.seller-private/signing.pem', 'certifi/signing.key', '.env',
                     'entitlement.json', 'purchase-order.json'):
            self.assertTrue(omit(PurePosixPath(name)), name)
        self.assertTrue(omit(PurePosixPath('certifi/cacert.pem'), source=True))

    def test_release_collection_keeps_real_ca_and_omits_signing_material(self):
        import certifi
        with tempfile.TemporaryDirectory() as name:
            bundle = Path(name)
            certificate = bundle / 'certifi/cacert.pem'
            certificate.parent.mkdir()
            certificate.write_bytes(Path(certifi.where()).read_bytes())
            (bundle / 'certifi/private.pem').write_text('private signing material', encoding='utf-8')
            (bundle / 'merchant.key').write_text('merchant material', encoding='utf-8')
            with patch('scripts.package_portable.dependency_notices', return_value=({}, [])):
                files = collect(bundle)
            self.assertEqual(files['certifi/cacert.pem'].read_bytes(), Path(certifi.where()).read_bytes())
            self.assertNotIn('certifi/private.pem', files)
            self.assertNotIn('merchant.key', files)


if __name__ == '__main__':
    unittest.main()
