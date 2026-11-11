package app.novalabs.orbit

import java.math.BigInteger
import java.security.MessageDigest

/**
 * Checking an Ed25519 signature (RFC 8032, section 5.1.7) in plain Kotlin, so every Android version can check
 * Nova's release signature: Android itself only has Ed25519 from version 13. It is the RFC's reference algorithm,
 * the same one Orbit for desktop uses, and it only verifies (no secret key is ever involved).
 */
object Ed25519 {
    private val ZERO = BigInteger.ZERO
    private val ONE = BigInteger.ONE
    private val TWO = BigInteger.valueOf(2)
    private val P: BigInteger = ONE.shiftLeft(255).subtract(BigInteger.valueOf(19))
    private val Q: BigInteger = ONE.shiftLeft(252).add(BigInteger("27742317777372353535851937790883648493"))
    private val D: BigInteger = BigInteger.valueOf(-121665).multiply(BigInteger.valueOf(121666).modInverse(P)).mod(P)
    private val SQRT_M1: BigInteger = TWO.modPow(P.subtract(ONE).shiftRight(2), P)

    // The key inside an Ed25519 "PUBLIC KEY" (X.509 SubjectPublicKeyInfo): this fixed header, then 32 key bytes.
    private val SPKI_PREFIX = byteArrayOf(0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00)

    private class Pt(val x: BigInteger, val y: BigInteger, val z: BigInteger, val t: BigInteger)

    private fun add(a: Pt, b: Pt): Pt {
        val aa = a.y.subtract(a.x).multiply(b.y.subtract(b.x)).mod(P)
        val bb = a.y.add(a.x).multiply(b.y.add(b.x)).mod(P)
        val cc = TWO.multiply(a.t).multiply(b.t).multiply(D).mod(P)
        val dd = TWO.multiply(a.z).multiply(b.z).mod(P)
        val e = bb.subtract(aa)
        val f = dd.subtract(cc)
        val g = dd.add(cc)
        val h = bb.add(aa)
        return Pt(e.multiply(f).mod(P), g.multiply(h).mod(P), f.multiply(g).mod(P), e.multiply(h).mod(P))
    }

    private fun mul(s0: BigInteger, p0: Pt): Pt {
        var s = s0
        var p = p0
        var q = Pt(ZERO, ONE, ONE, ZERO)
        while (s.signum() > 0) {
            if (s.testBit(0)) q = add(q, p)
            p = add(p, p)
            s = s.shiftRight(1)
        }
        return q
    }

    private fun same(a: Pt, b: Pt): Boolean =
        a.x.multiply(b.z).subtract(b.x.multiply(a.z)).mod(P).signum() == 0 &&
            a.y.multiply(b.z).subtract(b.y.multiply(a.z)).mod(P).signum() == 0

    private fun recoverX(y: BigInteger, sign: Int): BigInteger? {
        if (y >= P) return null
        val den = D.multiply(y).multiply(y).add(ONE).mod(P)
        if (den.signum() == 0) return null
        val x2 = y.multiply(y).subtract(ONE).multiply(den.modInverse(P)).mod(P)
        if (x2.signum() == 0) return if (sign != 0) null else ZERO
        var x = x2.modPow(P.add(BigInteger.valueOf(3)).shiftRight(3), P)
        if (x.multiply(x).subtract(x2).mod(P).signum() != 0) x = x.multiply(SQRT_M1).mod(P)
        if (x.multiply(x).subtract(x2).mod(P).signum() != 0) return null
        if ((if (x.testBit(0)) 1 else 0) != sign) x = P.subtract(x)
        return x
    }

    private fun littleEndian(b: ByteArray) = BigInteger(1, b.reversedArray())

    private fun decompress(b: ByteArray): Pt? {
        if (b.size != 32) return null
        var y = littleEndian(b)
        val sign = if (y.testBit(255)) 1 else 0
        y = y.clearBit(255)
        val x = recoverX(y, sign) ?: return null
        return Pt(x, y, ONE, x.multiply(y).mod(P))
    }

    private val G: Pt by lazy {
        val y = BigInteger.valueOf(4).multiply(BigInteger.valueOf(5).modInverse(P)).mod(P)
        val x = recoverX(y, 0)!!
        Pt(x, y, ONE, x.multiply(y).mod(P))
    }

    /** true when [signature] (64 bytes) is [publicKey]'s (32 bytes) signature of [message]. */
    fun verify(publicKey: ByteArray, message: ByteArray, signature: ByteArray): Boolean {
        if (publicKey.size != 32 || signature.size != 64) return false
        return try {
            val a = decompress(publicKey) ?: return false
            val r = decompress(signature.copyOfRange(0, 32)) ?: return false
            val s = littleEndian(signature.copyOfRange(32, 64))
            if (s >= Q) return false
            val md = MessageDigest.getInstance("SHA-512")
            md.update(signature, 0, 32)
            md.update(publicKey)
            md.update(message)
            val h = littleEndian(md.digest()).mod(Q)
            same(mul(s, G), add(r, mul(h, a)))
        } catch (e: ArithmeticException) {
            false
        }
    }

    /** The 32 key bytes of an Ed25519 public key in X.509 form (a PEM file's body, base64-decoded), or null. */
    fun keyFromSpki(der: ByteArray): ByteArray? =
        if (der.size == 44 && der.copyOfRange(0, 12).contentEquals(SPKI_PREFIX)) der.copyOfRange(12, 44) else null
}
