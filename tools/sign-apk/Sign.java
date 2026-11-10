import com.android.apksig.ApkSigner;
import com.android.apksig.ApkVerifier;
import java.io.File;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.cert.X509Certificate;
import java.util.List;

/** Sign.java KEYSTORE PASSWORD_FILE ALIAS IN.apk OUT.apk  — signs (v1+v2+v3) and verifies. */
public class Sign {
    public static void main(String[] a) throws Exception {
        char[] pw = Files.readString(Path.of(a[1])).trim().toCharArray();
        KeyStore ks = KeyStore.getInstance("PKCS12");
        try (InputStream s = Files.newInputStream(Path.of(a[0]))) { ks.load(s, pw); }
        PrivateKey key = (PrivateKey) ks.getKey(a[2], pw);
        X509Certificate cert = (X509Certificate) ks.getCertificate(a[2]);
        ApkSigner.SignerConfig sc = new ApkSigner.SignerConfig.Builder("ORBIT", key, List.of(cert)).build();
        new ApkSigner.Builder(List.of(sc)).setInputApk(new File(a[3])).setOutputApk(new File(a[4]))
            .setV1SigningEnabled(true).setV2SigningEnabled(true).setV3SigningEnabled(true).build().sign();
        ApkVerifier.Result r = new ApkVerifier.Builder(new File(a[4])).build().verify();
        System.out.println("verified=" + r.isVerified() + " v1=" + r.isVerifiedUsingV1Scheme()
            + " v2=" + r.isVerifiedUsingV2Scheme() + " v3=" + r.isVerifiedUsingV3Scheme());
        for (Object e : r.getErrors()) System.out.println("error: " + e);
        for (X509Certificate c : r.getSignerCertificates())
            System.out.println("signer sha256=" + hex(MessageDigest.getInstance("SHA-256").digest(c.getEncoded())));
        if (!r.isVerified()) System.exit(1);
    }

    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02X", x));
        return s.toString();
    }
}
