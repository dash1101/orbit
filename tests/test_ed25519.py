"""The built-in Ed25519 check: RFC 8032's test vectors, Nova's real release, and anything changed by one bit."""
import base64
import unittest

from orbit import ed25519
from orbit.install.engine import Failed
from orbit.install.recipes.nova import RELEASE_KEY, check_signature

# RFC 8032, section 7.1, tests 1 and 2: (public key, message, signature)
RFC = [("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
       ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")]

# Nova 0.9.16-alpha's published SHA256SUMS and SHA256SUMS.sig, signed with the release key Orbit has built in.
NOVA_SUMS = '0d194dea8a822d117f7e1b58076c805a1c7c88b9a6350e09dc15fd23722c12f3  nova-server_0.9.16~alpha_all.deb\n0d194dea8a822d117f7e1b58076c805a1c7c88b9a6350e09dc15fd23722c12f3  nova-server.deb\n5b36b38b4bb5dd81adf5a3c5b791f561b986059d0efb67651904352300b0878a  nova-server_0.9.16~alpha.tar.gz\nf8649119ed98af0ecde0e2fbb4577aab2e90103a7cd70e3381bb3bcd1bf052e9  nova-installer.run\nc177903a5450d45870f7589b42f8f378d5060fa87b35cbadbaf1de9d1c154045  nova-server_0.9.16-alpha_windows.zip\nc177903a5450d45870f7589b42f8f378d5060fa87b35cbadbaf1de9d1c154045  nova-windows.zip\n01473204579303be91d1f198a1b624d044745a0214bd718ed9c56327abbde72c  nova-0.9.16-alpha-arm64.apk\nb5ab686095143c11ad316953b9e761c859555b9b6fb9bbeefedeb9e37f186439  nova-0.9.16-alpha-universal.apk\nb5ab686095143c11ad316953b9e761c859555b9b6fb9bbeefedeb9e37f186439  nova-universal.apk\na47455119bc6f763c0bcb64c2a823b767ee799c46c58fc1e92ac436f3e427501  nova-wear-0.9.16-alpha.apk\na47455119bc6f763c0bcb64c2a823b767ee799c46c58fc1e92ac436f3e427501  nova-wear.apk\n'
NOVA_SIG = 'Lv+1QTDepZ6+xKavsXdGiwwa98FucCqAdYb7dB/UDwYgOYH0PGCHcZEMEs6buMA1nkwzl7rYvCjwxtV+DkQTDw=='


class Ed25519Tests(unittest.TestCase):
    def test_rfc_vectors(self):
        for pk, msg, sig in RFC:
            self.assertTrue(ed25519.verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig)))

    def test_any_changed_bit_fails(self):
        pk, msg, sig = (bytes.fromhex(x) for x in RFC[1])
        for i in range(64):
            bad = bytearray(sig)
            bad[i] ^= 1 << (i % 8)
            self.assertFalse(ed25519.verify(pk, msg, bytes(bad)), i)
        self.assertFalse(ed25519.verify(pk, msg + b"!", sig))
        self.assertFalse(ed25519.verify(bytes.fromhex(RFC[0][0]), msg, sig))     # someone else's key

    def test_bad_lengths_and_points(self):
        pk, msg, sig = (bytes.fromhex(x) for x in RFC[0])
        self.assertFalse(ed25519.verify(pk[:31], msg, sig))
        self.assertFalse(ed25519.verify(pk, msg, sig[:63]))
        self.assertFalse(ed25519.verify(b"\xff" * 32, msg, sig))               # not a point on the curve
        s_too_big = sig[:32] + (2 ** 253).to_bytes(32, "little")
        self.assertFalse(ed25519.verify(pk, msg, s_too_big))

    def test_pem_key(self):
        self.assertEqual(len(ed25519.key_from_pem(RELEASE_KEY)), 32)
        with self.assertRaises(ValueError):
            ed25519.key_from_pem("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----")

    def test_novas_real_release(self):
        check_signature(NOVA_SUMS, NOVA_SIG, RELEASE_KEY)          # raises when it doesn't check out
        with self.assertRaises(Failed):
            check_signature(NOVA_SUMS.replace("a", "b", 1), NOVA_SIG, RELEASE_KEY)
        with self.assertRaises(Failed):
            check_signature(NOVA_SUMS, "not base64!", RELEASE_KEY)
        raw = bytearray(base64.b64decode(NOVA_SIG))
        raw[0] ^= 1
        with self.assertRaises(Failed):
            check_signature(NOVA_SUMS, base64.b64encode(bytes(raw)).decode(), RELEASE_KEY)


if __name__ == "__main__":
    unittest.main()
