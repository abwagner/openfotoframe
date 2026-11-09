import Foundation
import StoreKit
import SwiftUI

struct ApplePurchaseIntent: Decodable {
    let appAccountToken: String
    let productId: String
    let planId: String
    let checkoutReference: String
}

struct AppleTransactionProof: Identifiable {
    var id: UInt64 { transaction.id }
    let transaction: StoreKit.Transaction
    let signedTransaction: String
}

/// Transaction proof is sent only to this build's configured central service.
/// A self-hosted or link-supplied destination can never receive App Store proof.
enum StoreKitPurchaseRules {
    static func permits(origin: String?, configuredHostedOrigin: String?) -> Bool {
        guard let origin, let configuredHostedOrigin,
              let current = try? FrameServer(origin), let configured = try? FrameServer(configuredHostedOrigin),
              current.url.scheme == "https", configured.url.scheme == "https" else { return false }
        return current.origin == configured.origin
    }
    static func accountToken(_ intent: ApplePurchaseIntent, expectedProduct: String) throws -> UUID {
        guard intent.productId == expectedProduct, let token = UUID(uuidString: intent.appAccountToken) else { throw FrameError.message("The service returned an invalid App Store purchase request.") }
        return token
    }
}

@MainActor
final class StoreKitPurchases: ObservableObject {
    @Published private(set) var products: [String: Product] = [:]
    @Published private(set) var pending: [UInt64: AppleTransactionProof] = [:]
    @Published private(set) var verificationFailure = false
    private var listener: Task<Void, Never>?
    private var unfinished: Task<Void, Never>?

    init(observeTransactions: Bool = true) {
        guard observeTransactions else { return }
        listener = Task { [weak self] in
            for await result in StoreKit.Transaction.updates {
                guard !Task.isCancelled else { return }
                self?.receive(result)
            }
        }
        unfinished = Task { [weak self] in
            for await result in StoreKit.Transaction.unfinished {
                guard !Task.isCancelled else { return }
                self?.receive(result)
            }
        }
    }
    deinit { listener?.cancel(); unfinished?.cancel() }

    func loadProducts(_ identifiers: Set<String>) async throws {
        guard !identifiers.isEmpty else { products = [:]; return }
        let values = try await Product.products(for: identifiers)
        products = Dictionary(uniqueKeysWithValues: values.map { ($0.id, $0) })
    }
    func purchase(productID: String, accountToken: UUID) async throws -> AppleTransactionProof? {
        guard let product = products[productID], product.type == .autoRenewable else { throw FrameError.message("This subscription is not available in the App Store right now.") }
        let result = try await product.purchase(options: [.appAccountToken(accountToken)])
        switch result {
        case .success(let verification):
            guard case .verified(let transaction) = verification,
                  transaction.productID == productID, transaction.appAccountToken == accountToken else {
                throw FrameError.message("The App Store transaction could not be verified for this household. No service access has been granted.")
            }
            let proof = AppleTransactionProof(transaction: transaction, signedTransaction: verification.jwsRepresentation)
            pending[proof.id] = proof
            return proof
        case .pending:
            return nil
        case .userCancelled:
            throw CancellationError()
        @unknown default:
            throw FrameError.message("The App Store returned an unsupported purchase result.")
        }
    }
    func manageSubscriptions() async throws {
        guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first(where: { $0.activationState == .foregroundActive }) else { throw FrameError.message("Open the app before managing your subscription.") }
        try await AppStore.showManageSubscriptions(in: scene)
    }
    func restore(productIDs: Set<String>, accountToken: UUID) async throws -> [AppleTransactionProof] {
        // sync may show Apple's account UI, so it is called only from Restore Purchases.
        try await AppStore.sync()
        var results: [AppleTransactionProof] = []
        for await verification in StoreKit.Transaction.currentEntitlements {
            guard case .verified(let transaction) = verification else { verificationFailure = true; continue }
            guard productIDs.contains(transaction.productID), transaction.appAccountToken == accountToken else { continue }
            let proof = AppleTransactionProof(transaction: transaction, signedTransaction: verification.jwsRepresentation)
            pending[proof.id] = proof; results.append(proof)
        }
        return results
    }
    func matchingPending(productID: String, accountToken: UUID) -> [AppleTransactionProof] {
        pending.values.filter { $0.transaction.productID == productID && $0.transaction.appAccountToken == accountToken }
    }
    func finish(_ proof: AppleTransactionProof) async {
        // Call only after the backend has verified and acknowledged this transaction.
        await proof.transaction.finish()
        pending.removeValue(forKey: proof.id)
    }
    private func receive(_ result: VerificationResult<StoreKit.Transaction>) {
        guard case .verified(let transaction) = result else { verificationFailure = true; return }
        pending[transaction.id] = AppleTransactionProof(transaction: transaction, signedTransaction: result.jwsRepresentation)
    }
}
