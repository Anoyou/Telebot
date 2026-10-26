import Foundation
#if canImport(CryptoKit)
import CryptoKit
#endif

/// NaCl `crypto_box` 解密（Curve25519 + XSalsa20 + Poly1305），与 Go `golang.org/x/crypto/nacl/box` 兼容。
///
/// 只用于 Cloudflare Access 原生登录：cloudflared 的令牌转交服务（login.cloudflareaccess.org）把 Access
/// 令牌用 NaCl box 加密给本机临时生成的公钥。X25519 由 CryptoKit 提供；CryptoKit 没有 XSalsa20／Poly1305，
/// 这里按 NaCl 规范实现（只解密、常量时间比较认证标签）。
enum NaClBox {
    static let nonceLength = 24
    static let tagLength = 16

    // MARK: crypto_box_open

    /// 解开 Go `box.Seal` 的输出。`message` = 24 字节 nonce + 16 字节标签 + 密文（cloudflared 的格式）。
    /// - Parameter sharedSecret: X25519(本机私钥, 发送方公钥) 的原始 32 字节。
    static func open(message: [UInt8], sharedSecret: [UInt8]) -> [UInt8]? {
        guard message.count >= nonceLength + tagLength, sharedSecret.count == 32 else { return nil }
        let nonce = Array(message[0..<nonceLength])
        let boxed = Array(message[nonceLength...])
        // crypto_box_beforenm：k = HSalsa20(shared, 0)
        let key = hsalsa20(key: sharedSecret, input: [UInt8](repeating: 0, count: 16))
        return secretboxOpen(boxed: boxed, nonce: nonce, key: key)
    }

    /// crypto_secretbox_open（XSalsa20-Poly1305），`boxed` = 16 字节标签 + 密文。
    static func secretboxOpen(boxed: [UInt8], nonce: [UInt8], key: [UInt8]) -> [UInt8]? {
        guard boxed.count >= tagLength, nonce.count == nonceLength, key.count == 32 else { return nil }
        let subkey = hsalsa20(key: key, input: Array(nonce[0..<16]))
        let streamNonce = Array(nonce[16..<24])
        let tag = Array(boxed[0..<tagLength])
        let ciphertext = Array(boxed[tagLength...])
        // 密钥流前 32 字节是 Poly1305 一次性密钥，其后与密文异或。
        let stream = salsa20Stream(key: subkey, nonce: streamNonce, count: 32 + ciphertext.count)
        let polyKey = Array(stream[0..<32])
        guard constantTimeEqual(poly1305(message: ciphertext, key: polyKey), tag) else { return nil }
        var plain = [UInt8](repeating: 0, count: ciphertext.count)
        for index in 0..<ciphertext.count { plain[index] = ciphertext[index] ^ stream[32 + index] }
        return plain
    }

    static func constantTimeEqual(_ lhs: [UInt8], _ rhs: [UInt8]) -> Bool {
        guard lhs.count == rhs.count else { return false }
        var diff: UInt8 = 0
        for index in 0..<lhs.count { diff |= lhs[index] ^ rhs[index] }
        return diff == 0
    }

    // MARK: Salsa20

    private static let sigma: [UInt32] = [0x6170_7865, 0x3320_646e, 0x7962_2d32, 0x6b20_6574] // "expand 32-byte k"

    private static func load32(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
        UInt32(bytes[offset]) | UInt32(bytes[offset + 1]) << 8 | UInt32(bytes[offset + 2]) << 16 | UInt32(bytes[offset + 3]) << 24
    }

    private static func rotl(_ value: UInt32, _ shift: UInt32) -> UInt32 { (value << shift) | (value >> (32 - shift)) }

    private static func doubleRounds(_ x: inout [UInt32]) {
        for _ in 0..<10 {
            x[4] ^= rotl(x[0] &+ x[12], 7); x[8] ^= rotl(x[4] &+ x[0], 9)
            x[12] ^= rotl(x[8] &+ x[4], 13); x[0] ^= rotl(x[12] &+ x[8], 18)
            x[9] ^= rotl(x[5] &+ x[1], 7); x[13] ^= rotl(x[9] &+ x[5], 9)
            x[1] ^= rotl(x[13] &+ x[9], 13); x[5] ^= rotl(x[1] &+ x[13], 18)
            x[14] ^= rotl(x[10] &+ x[6], 7); x[2] ^= rotl(x[14] &+ x[10], 9)
            x[6] ^= rotl(x[2] &+ x[14], 13); x[10] ^= rotl(x[6] &+ x[2], 18)
            x[3] ^= rotl(x[15] &+ x[11], 7); x[7] ^= rotl(x[3] &+ x[15], 9)
            x[11] ^= rotl(x[7] &+ x[3], 13); x[15] ^= rotl(x[11] &+ x[7], 18)
            x[1] ^= rotl(x[0] &+ x[3], 7); x[2] ^= rotl(x[1] &+ x[0], 9)
            x[3] ^= rotl(x[2] &+ x[1], 13); x[0] ^= rotl(x[3] &+ x[2], 18)
            x[6] ^= rotl(x[5] &+ x[4], 7); x[7] ^= rotl(x[6] &+ x[5], 9)
            x[4] ^= rotl(x[7] &+ x[6], 13); x[5] ^= rotl(x[4] &+ x[7], 18)
            x[11] ^= rotl(x[10] &+ x[9], 7); x[8] ^= rotl(x[11] &+ x[10], 9)
            x[9] ^= rotl(x[8] &+ x[11], 13); x[10] ^= rotl(x[9] &+ x[8], 18)
            x[12] ^= rotl(x[15] &+ x[14], 7); x[13] ^= rotl(x[12] &+ x[15], 9)
            x[14] ^= rotl(x[13] &+ x[12], 13); x[15] ^= rotl(x[14] &+ x[13], 18)
        }
    }

    private static func initialState(key: [UInt8], input: [UInt8]) -> [UInt32] {
        [sigma[0], load32(key, 0), load32(key, 4), load32(key, 8), load32(key, 12), sigma[1],
         load32(input, 0), load32(input, 4), load32(input, 8), load32(input, 12),
         sigma[2], load32(key, 16), load32(key, 20), load32(key, 24), load32(key, 28), sigma[3]]
    }

    /// HSalsa20：32 字节密钥 + 16 字节输入 → 32 字节子密钥。
    static func hsalsa20(key: [UInt8], input: [UInt8]) -> [UInt8] {
        var x = initialState(key: key, input: input)
        doubleRounds(&x)
        var out: [UInt8] = []
        out.reserveCapacity(32)
        for index in [0, 5, 10, 15, 6, 7, 8, 9] {
            let word = x[index]
            out += [UInt8(truncatingIfNeeded: word), UInt8(truncatingIfNeeded: word >> 8),
                    UInt8(truncatingIfNeeded: word >> 16), UInt8(truncatingIfNeeded: word >> 24)]
        }
        return out
    }

    /// Salsa20 密钥流（8 字节 nonce，块计数器从 0 开始）。
    static func salsa20Stream(key: [UInt8], nonce: [UInt8], count: Int) -> [UInt8] {
        var out: [UInt8] = []
        out.reserveCapacity(count + 64)
        var counter: UInt64 = 0
        while out.count < count {
            var input = nonce
            for shift in stride(from: 0, to: 64, by: 8) { input.append(UInt8(truncatingIfNeeded: counter >> UInt64(shift))) }
            let state = initialState(key: key, input: input)
            var x = state
            doubleRounds(&x)
            for index in 0..<16 {
                let word = x[index] &+ state[index]
                out += [UInt8(truncatingIfNeeded: word), UInt8(truncatingIfNeeded: word >> 8),
                        UInt8(truncatingIfNeeded: word >> 16), UInt8(truncatingIfNeeded: word >> 24)]
            }
            counter &+= 1
        }
        return Array(out.prefix(count))
    }

    // MARK: Poly1305（26 位分组，参考 poly1305-donna-32）

    static func poly1305(message: [UInt8], key: [UInt8]) -> [UInt8] {
        let r0 = UInt64(load32(key, 0)) & 0x3ff_ffff
        let r1 = UInt64(load32(key, 3) >> 2) & 0x3ff_ff03
        let r2 = UInt64(load32(key, 6) >> 4) & 0x3ff_c0ff
        let r3 = UInt64(load32(key, 9) >> 6) & 0x3f0_3fff
        let r4 = UInt64(load32(key, 12) >> 8) & 0x00f_ffff
        let s1 = r1 * 5, s2 = r2 * 5, s3 = r3 * 5, s4 = r4 * 5
        var h0: UInt64 = 0, h1: UInt64 = 0, h2: UInt64 = 0, h3: UInt64 = 0, h4: UInt64 = 0

        var offset = 0
        while offset < message.count {
            var block = [UInt8](repeating: 0, count: 17)
            let length = min(16, message.count - offset)
            for index in 0..<length { block[index] = message[offset + index] }
            let hibit: UInt64
            if length == 16 { hibit = 1 << 24 } else { block[length] = 1; hibit = 0 }
            h0 += UInt64(load32(block, 0)) & 0x3ff_ffff
            h1 += UInt64(load32(block, 3) >> 2) & 0x3ff_ffff
            h2 += UInt64(load32(block, 6) >> 4) & 0x3ff_ffff
            h3 += UInt64(load32(block, 9) >> 6) & 0x3ff_ffff
            h4 += UInt64(load32(block, 12) >> 8) | hibit

            let d0 = h0 * r0 + h1 * s4 + h2 * s3 + h3 * s2 + h4 * s1
            var d1 = h0 * r1 + h1 * r0 + h2 * s4 + h3 * s3 + h4 * s2
            var d2 = h0 * r2 + h1 * r1 + h2 * r0 + h3 * s4 + h4 * s3
            var d3 = h0 * r3 + h1 * r2 + h2 * r1 + h3 * r0 + h4 * s4
            var d4 = h0 * r4 + h1 * r3 + h2 * r2 + h3 * r1 + h4 * r0

            var carry = d0 >> 26; h0 = d0 & 0x3ff_ffff
            d1 += carry; carry = d1 >> 26; h1 = d1 & 0x3ff_ffff
            d2 += carry; carry = d2 >> 26; h2 = d2 & 0x3ff_ffff
            d3 += carry; carry = d3 >> 26; h3 = d3 & 0x3ff_ffff
            d4 += carry; carry = d4 >> 26; h4 = d4 & 0x3ff_ffff
            h0 += carry * 5; carry = h0 >> 26; h0 &= 0x3ff_ffff
            h1 += carry
            offset += 16
        }

        // 完全进位并对 2^130 - 5 取模。
        var carry = h1 >> 26; h1 &= 0x3ff_ffff
        h2 += carry; carry = h2 >> 26; h2 &= 0x3ff_ffff
        h3 += carry; carry = h3 >> 26; h3 &= 0x3ff_ffff
        h4 += carry; carry = h4 >> 26; h4 &= 0x3ff_ffff
        h0 += carry * 5; carry = h0 >> 26; h0 &= 0x3ff_ffff
        h1 += carry

        var g0 = h0 + 5; carry = g0 >> 26; g0 &= 0x3ff_ffff
        var g1 = h1 + carry; carry = g1 >> 26; g1 &= 0x3ff_ffff
        var g2 = h2 + carry; carry = g2 >> 26; g2 &= 0x3ff_ffff
        var g3 = h3 + carry; carry = g3 >> 26; g3 &= 0x3ff_ffff
        let g4 = h4 &+ carry &- (1 << 26)

        // g4 的最高位为 0 表示 h >= p，取 g；否则保留 h（无分支选择）。
        let mask = (g4 >> 63) &- 1 // h >= p 时全 1
        h0 = (h0 & ~mask) | (g0 & mask)
        h1 = (h1 & ~mask) | (g1 & mask)
        h2 = (h2 & ~mask) | (g2 & mask)
        h3 = (h3 & ~mask) | (g3 & mask)
        h4 = (h4 & ~mask) | (g4 & mask & 0x3ff_ffff)

        // h = h % 2^128，再加上 s（密钥后 16 字节）。
        let w0 = (h0 | (h1 << 26)) & 0xffff_ffff
        let w1 = ((h1 >> 6) | (h2 << 20)) & 0xffff_ffff
        let w2 = ((h2 >> 12) | (h3 << 14)) & 0xffff_ffff
        let w3 = ((h3 >> 18) | (h4 << 8)) & 0xffff_ffff

        var f = w0 + UInt64(load32(key, 16)); let o0 = f & 0xffff_ffff
        f = w1 + UInt64(load32(key, 20)) + (f >> 32); let o1 = f & 0xffff_ffff
        f = w2 + UInt64(load32(key, 24)) + (f >> 32); let o2 = f & 0xffff_ffff
        f = w3 + UInt64(load32(key, 28)) + (f >> 32); let o3 = f & 0xffff_ffff

        var tag: [UInt8] = []
        for word in [o0, o1, o2, o3] {
            tag += [UInt8(truncatingIfNeeded: word), UInt8(truncatingIfNeeded: word >> 8),
                    UInt8(truncatingIfNeeded: word >> 16), UInt8(truncatingIfNeeded: word >> 24)]
        }
        return tag
    }
}

#if canImport(CryptoKit)
/// 一次性的 X25519 密钥对：公钥作为转交服务的取件凭据，私钥只在内存里，用后即弃（与 cloudflared 一致）。
struct NaClKeyPair: @unchecked Sendable {
    let privateKey: Curve25519.KeyAgreement.PrivateKey

    init() { privateKey = Curve25519.KeyAgreement.PrivateKey() }

    /// 测试用：固定私钥。
    init(rawPrivateKey: Data) throws { privateKey = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: rawPrivateKey) }

    /// Go `base64.URLEncoding`（URL 安全字母表，保留 `=` 填充）。
    var publicKeyBase64URL: String { NaClKeyPair.base64URL(privateKey.publicKey.rawRepresentation) }

    static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
    }

    static func decodeBase64URL(_ text: String) -> Data? {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while value.count % 4 != 0 { value += "=" }
        return Data(base64Encoded: value)
    }

    /// 解密转交服务返回的数据：`senderPublicKey` 是响应头 `service-public-key`。
    func open(_ message: Data, senderPublicKey: String) -> Data? {
        guard let senderRaw = Self.decodeBase64URL(senderPublicKey), senderRaw.count == 32,
              let sender = try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: senderRaw),
              let shared = try? privateKey.sharedSecretFromKeyAgreement(with: sender) else { return nil }
        let secret = shared.withUnsafeBytes { Array($0) }
        return NaClBox.open(message: Array(message), sharedSecret: secret).map { Data($0) }
    }
}
#endif
