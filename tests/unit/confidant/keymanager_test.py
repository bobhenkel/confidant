import unittest
import datetime
import json
from mock import patch
from mock import MagicMock

from confidant import keymanager
from confidant.app import app
from confidant.utils import lru


class KeyManagerTest(unittest.TestCase):
    def setUp(self):
        self.use_auth = app.config['USE_AUTH']
        self.use_encryption = app.config['USE_ENCRYPTION']
        self.scoped_auth_keys = app.config['SCOPED_AUTH_KEYS']

    def tearDown(self):
        app.config['USE_AUTH'] = self.use_auth
        app.config['USE_ENCRYPTION'] = self.use_encryption
        app.config['SCOPED_AUTH_KEYS'] = self.scoped_auth_keys

    @patch('confidant.keymanager.KEY_METADATA', {})
    @patch('confidant.keymanager.auth_kms_client.describe_key')
    def test_get_key_arn(self, kms_mock):
        kms_mock.return_value = {'KeyMetadata': {'Arn': 'mocked:arn'}}
        self.assertEqual(
            keymanager.get_key_arn('mockalias'),
            'mocked:arn'
        )

    @patch(
        'confidant.keymanager.KEY_METADATA',
        {'mockalias': {'KeyMetadata': {'Arn': 'mocked:arn'}}}
    )
    @patch('confidant.keymanager.auth_kms_client.describe_key')
    def test_get_key_arn_cached(self, kms_mock):
        self.assertEqual(
            keymanager.get_key_arn('mockalias'),
            'mocked:arn'
        )

    @patch('confidant.keymanager.KEY_METADATA', {})
    @patch('confidant.keymanager.auth_kms_client.describe_key')
    def test_get_key_id(self, kms_mock):
        kms_mock.return_value = {'KeyMetadata': {'KeyId': 'mockid'}}
        self.assertEqual(
            keymanager.get_key_id('mockalias'),
            'mockid'
        )

    @patch(
        'confidant.keymanager.KEY_METADATA',
        {'mockalias': {'KeyMetadata': {'KeyId': 'mockid'}}}
    )
    @patch('confidant.keymanager.auth_kms_client.describe_key')
    def test_get_key_id_cached(self, kms_mock):
        self.assertEqual(
            keymanager.get_key_id('mockalias'),
            'mockid'
        )

    @patch('cryptography.fernet.Fernet.generate_key')
    def test_create_datakey_mocked(self, fernet_mock):
        app.config['USE_ENCRYPTION'] = False
        fernet_mock.return_value = 'mocked_fernet_key'

        ret = keymanager.create_datakey({})

        self.assertTrue(fernet_mock.called)

        # Assert that we got a dict returned where the ciphertext and plaintext
        # keys are equal
        self.assertEquals(ret['ciphertext'], ret['plaintext'])

        # Assert ciphertext is mocked_fernet_key
        self.assertEquals(ret['ciphertext'], 'mocked_fernet_key')

    def test_decrypt_datakey_mocked(self):
        app.config['USE_ENCRYPTION'] = False
        ret = keymanager.decrypt_datakey('mocked_fernet_key')

        # Ensure we get the same value out that we sent in.
        self.assertEquals(ret, 'mocked_fernet_key')

    @patch(
        'confidant.keymanager.get_key_arn'
    )
    def test_valid_service_auth_key(self, gka_mock):
        # Test AUTH_KEY arn checking
        gka_mock.side_effect = ['test::arn']
        self.assertTrue(keymanager.valid_service_auth_key('test::arn'))
        # Test SCOPED_AUTH_KEYS arn checking. There's two items in the side
        # effect because get_key_arn will be called twice: once for AUTH_KEY
        # check (which will fail) and another for the SCOPED_AUTH_KEYS check.
        gka_mock.side_effect = ['auth::key', 'test::arn']
        app.config['SCOPED_AUTH_KEYS'] = {'test-key': 'test-account'}
        self.assertTrue(keymanager.valid_service_auth_key('test::arn'))
        # Test failure mode, where both AUTH_KEY and SCOPED_AUTH_KEYS checks
        # fail. We have two items in side effects because get_key_arn will be
        # called twice.
        gka_mock.side_effect = ['auth::key', 'test::arn']
        self.assertFalse(keymanager.valid_service_auth_key('bad::arn'))

    @patch(
        'confidant.keymanager.cryptolib.create_datakey'
    )
    @patch(
        'confidant.keymanager.cryptolib.create_mock_datakey'
    )
    def test_create_datakey_with_encryption(self, cmd_mock, cd_mock):
        app.config['USE_ENCRYPTION'] = True
        context = {'from': 'confidant-development',
                   'to': 'confidant-development'}
        keymanager.create_datakey(context)

        # Assert that create_datakey was called and create_mock_datakey was
        # not called.
        self.assertTrue(cd_mock.called)
        self.assertFalse(cmd_mock.called)

    @patch(
        'confidant.keymanager.cryptolib.decrypt_datakey'
    )
    @patch(
        'confidant.keymanager.cryptolib.decrypt_mock_datakey'
    )
    def test_decrypt_datakey_with_encryption(self, dmd_mock, dd_mock):
        app.config['USE_ENCRYPTION'] = True
        context = {'from': 'confidant-development',
                   'to': 'confidant-development'}
        keymanager.decrypt_datakey('encrypted', context)

        # Assert that decrypt_datakey was called and decrypt_mock_datakey was
        # not called.
        self.assertTrue(dd_mock.called)
        self.assertFalse(dmd_mock.called)

    @patch(
        'confidant.keymanager.get_key_arn',
        MagicMock(return_value='mocked')
    )
    @patch(
        'confidant.keymanager.get_key_alias_from_cache',
        MagicMock(return_value='authnz-testing')
    )
    @patch('confidant.keymanager.at_rest_kms_client.decrypt')
    def test_decrypt_token(self, kms_mock):
        time_format = "%Y%m%dT%H%M%SZ"
        now = datetime.datetime.utcnow()
        not_before = now.strftime(time_format)
        _not_after = now + datetime.timedelta(minutes=60)
        not_after = _not_after.strftime(time_format)
        payload = json.dumps({
            'not_before': not_before,
            'not_after': not_after
        })
        kms_mock.return_value = {
            'Plaintext': payload,
            'KeyId': 'mocked'
        }
        self.assertEqual(
            keymanager.decrypt_token(
                1,
                'service',
                'confidant-unittest',
                'ZW5jcnlwdGVk'
            ),
            {
                'payload': json.loads(payload),
                'key_alias': 'authnz-testing'
            }
        )
        keymanager.TOKENS = lru.LRUCache(4096)
        self.assertEqual(
            keymanager.decrypt_token(
                2,
                'user',
                'testuser',
                'ZW5jcnlwdGVk'
            ),
            {
                'payload': json.loads(payload),
                'key_alias': 'authnz-testing'
            }
        )
        keymanager.TOKENS = lru.LRUCache(4096)
        with self.assertRaisesRegexp(
                keymanager.TokenDecryptionError,
                'Unacceptable token version.'):
            keymanager.decrypt_token(
                3,
                'user',
                'testuser',
                'ZW5jcnlwdGVk'
            )
        with self.assertRaisesRegexp(
                keymanager.TokenDecryptionError,
                'Authentication error. Unsupported user_type.'):
            keymanager.decrypt_token(
                2,
                'unsupported',
                'testuser',
                'ZW5jcnlwdGVk'
            )
        # Missing KeyId, will cause an exception to be thrown
        kms_mock.return_value = {
            'Plaintext': payload
        }
        with self.assertRaisesRegexp(
                keymanager.TokenDecryptionError,
                'Authentication error. General error.'):
            keymanager.decrypt_token(
                2,
                'service',
                'confidant-unittest',
                'ZW5jcnlwdGVk'
            )
        # Payload missing not_before/not_after
        empty_payload = json.dumps({})
        kms_mock.return_value = {
            'Plaintext': empty_payload,
            'KeyId': 'mocked'
        }
        with self.assertRaisesRegexp(
                keymanager.TokenDecryptionError,
                'Authentication error. Missing validity.'):
            keymanager.decrypt_token(
                2,
                'service',
                'confidant-unittest',
                'ZW5jcnlwdGVk'
            )
        # lifetime of 0 will make every token invalid. testing for proper delta
        # checking.
        lifetime = app.config['AUTH_TOKEN_MAX_LIFETIME']
        app.config['AUTH_TOKEN_MAX_LIFETIME'] = 0
        kms_mock.return_value = {
            'Plaintext': payload,
            'KeyId': 'mocked'
        }
        with self.assertRaisesRegexp(
                keymanager.TokenDecryptionError,
                'Authentication error. Token lifetime exceeded.'):
            keymanager.decrypt_token(
                2,
                'service',
                'confidant-unittest',
                'ZW5jcnlwdGVk'
            )
        app.config['AUTH_TOKEN_MAX_LIFETIME'] = lifetime
        # Token too old
        now = datetime.datetime.utcnow()
        _not_before = now - datetime.timedelta(minutes=60)
        not_before = _not_before.strftime(time_format)
        _not_after = now - datetime.timedelta(minutes=1)
        not_after = _not_after.strftime(time_format)
        payload = json.dumps({
            'not_before': not_before,
            'not_after': not_after
        })
        kms_mock.return_value = {
            'Plaintext': payload,
            'KeyId': 'mocked'
        }
        with self.assertRaisesRegexp(
                keymanager.TokenDecryptionError,
                'Authentication error. Invalid time validity for token'):
            keymanager.decrypt_token(
                2,
                'service',
                'confidant-unittest',
                'ZW5jcnlwdGVk'
            )
        # Token too young
        now = datetime.datetime.utcnow()
        _not_before = now + datetime.timedelta(minutes=60)
        not_before = _not_before.strftime(time_format)
        _not_after = now + datetime.timedelta(minutes=120)
        not_after = _not_after.strftime(time_format)
        payload = json.dumps({
            'not_before': not_before,
            'not_after': not_after
        })
        kms_mock.return_value = {
            'Plaintext': payload,
            'KeyId': 'mocked'
        }
        with self.assertRaisesRegexp(
                keymanager.TokenDecryptionError,
                'Authentication error. Invalid time validity for token'):
            keymanager.decrypt_token(
                2,
                'service',
                'confidant-unittest',
                'ZW5jcnlwdGVk'
            )
