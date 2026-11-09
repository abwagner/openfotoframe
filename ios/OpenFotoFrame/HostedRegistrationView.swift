import SwiftUI

struct PaidServiceSection: View {
    @EnvironmentObject private var connection: ReleaseSession
    @EnvironmentObject private var purchases: StoreKitPurchases
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    let household: ReleaseHousehold?
    @State private var plans: [ReleasePlan] = []
    @State private var selectedPlan = ""
    @State private var name = ""
    @State private var createdHouseholdID: String?
    @State private var checkoutURL: URL?
    @State private var confirmCheckout = false
    @State private var entitlement: ReleaseEntitlement?
    @State private var failure: String?
    @State private var busy = false
    @State private var requestKey = UUID().uuidString
    @State private var appleIntent: ApplePurchaseIntent?
    @State private var purchaseMessage: String?
    var body: some View {
        Section(household == nil ? "Start Your Hosted Household" : "Hosted Service Plan") {
            if let entitlement {
                LabeledContent("Subscription", value: entitlement.active ? "Active" : entitlement.status.replacingOccurrences(of: "_", with: " ").capitalized)
                if entitlement.provider == "apple" && appleDestinationAllowed {
                    Button("Manage Apple Subscription") { Task { do { try await purchases.manageSubscriptions(); await refresh() } catch { failure = error.localizedDescription } } }
                }
            }
            if plans.isEmpty {
                Text("Paid registration is not available on this server yet. You can still join through an invitation.").font(.footnote).foregroundStyle(.secondary)
            } else {
                if household == nil { TextField("Household name", text: $name) }
                Picker("Plan", selection: $selectedPlan) {
                    Text("Choose a plan").tag("")
                    ForEach(plans) { plan in Text(plan.name + (plan.priceLabel.map { " — \($0)" } ?? "")).tag(plan.id) }
                }
                if let plan = plans.first(where: { $0.id == selectedPlan }), let description = plan.description { Text(description).font(.footnote).foregroundStyle(.secondary) }
                if let plan = selectedPlanValue, plan.purchaseChannels?.contains("apple") == true, let productID = plan.appleProductId, appleDestinationAllowed {
                    if let product = purchases.products[productID] {
                        Button("Subscribe with Apple — \(product.displayPrice)") { Task { await purchaseWithApple(productID: productID) } }.disabled(purchaseDisabled || entitlement?.active == true)
                    } else { Text("This App Store subscription is not currently available.").font(.footnote).foregroundStyle(.secondary) }
                    Button("Restore Apple Purchases") { Task { await restoreApple(productID: productID) } }.disabled(purchaseDisabled)
                    if !purchases.pending.isEmpty { Button("Finish Pending Apple Purchases") { Task { await finishPendingApple(productID: productID) } }.disabled(purchaseDisabled) }
                }
                if externalCheckoutAllowed && selectedPlanValue?.purchaseChannels?.contains("stripe") == true {
                    Button("Continue to Secure Website Checkout") { Task { await checkout() } }.disabled(purchaseDisabled || entitlement?.active == true)
                }
                if let purchaseMessage { Text(purchaseMessage).font(.footnote).foregroundStyle(.secondary) }
                Text("Invited members use the household’s existing plan. Subscription status never changes your collection or frame permissions.").font(.footnote).foregroundStyle(.secondary)
            }
            if let failure { Text(failure).font(.footnote).foregroundStyle(.secondary) }
            Button("Refresh Subscription Status") { Task { await refresh() } }.disabled(busy)
        }
        .task { await loadPlans(); await refresh() }
        .onChange(of: selectedPlan) { _, _ in requestKey = UUID().uuidString; appleIntent = nil; purchaseMessage = nil }
        .onChange(of: scenePhase) { _, value in if value == .active { Task { await refresh() } } }
        .confirmationDialog("Continue to \(checkoutURL?.host ?? "checkout")?", isPresented: $confirmCheckout, titleVisibility: .visible) {
            if let checkoutURL { Button("Open Secure Checkout") { openURL(checkoutURL) } }
        } message: { Text("Checkout opens in your browser. Return here afterwards and refresh subscription status. This app does not send your sign-in credentials to the checkout site.") }
    }
    private var selectedPlanValue: ReleasePlan? { plans.first { $0.id == selectedPlan } }
    private var purchaseContext: String { "\(connection.scopeIdentity)|\(connection.profile?.id.uuidString ?? "")|\(connection.identity?.user.id ?? "")" }
    private var appleDestinationAllowed: Bool { StoreKitPurchaseRules.permits(origin: connection.profile?.origin, configuredHostedOrigin: connection.hostedAddress) }
    private var externalCheckoutAllowed: Bool { Bundle.main.object(forInfoDictionaryKey: "FrameAllowsExternalCheckout") as? Bool == true }
    private var purchaseDisabled: Bool { busy || selectedPlan.isEmpty || (household == nil && name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) || connection.identity?.user.emailVerified != true }
    private func loadPlans() async {
        do {
            struct Plans: Decodable { let plans: [ReleasePlan] }
            let result: Plans = try await connection.json("api/v1/plans", authorized: false)
            plans = result.plans
            if appleDestinationAllowed { try await purchases.loadProducts(Set(result.plans.compactMap(\.appleProductId))) }
        } catch { failure = error.localizedDescription }
    }
    private func checkout() async {
        busy = true; defer { busy = false }
        do {
            let hid = try await ensureHousehold()
            struct Checkout: Decodable { let checkoutUrl: String }
            let result: Checkout = try await connection.json("api/v1/households/\(hid)/billing/checkout", method: "POST", body: ["plan_id": selectedPlan], idempotencyKey: requestKey)
            guard let url = URL(string: result.checkoutUrl), let host = url.host, !host.isEmpty,
                  url.scheme == "https", url.user == nil, url.password == nil else { throw FrameError.message("The service returned an invalid checkout destination.") }
            checkoutURL = url; confirmCheckout = true; failure = nil
        } catch { failure = error.localizedDescription }
    }
    private func ensureHousehold() async throws -> String {
        if let existing = household?.id ?? createdHouseholdID { return existing }
        struct Created: Decodable { let id: String }
        let created: Created = try await connection.json("api/v1/households", method: "POST", body: ["name": name, "plan_id": selectedPlan])
        createdHouseholdID = created.id
        return created.id
    }
    private func intent(productID: String) async throws -> (String, ApplePurchaseIntent, UUID) {
        guard appleDestinationAllowed else { throw FrameError.message("Apple subscriptions are available only for this app’s configured hosted service.") }
        let hid = try await ensureHousehold()
        let value: ApplePurchaseIntent
        if let saved = appleIntent { value = saved }
        else {
            value = try await connection.json("api/v1/households/\(hid)/billing/apple/purchase-intent", method: "POST", body: ["plan_id": selectedPlan], idempotencyKey: requestKey)
            appleIntent = value
        }
        guard value.planId == selectedPlan else { throw FrameError.message("The App Store purchase request does not match this plan.") }
        return (hid, value, try StoreKitPurchaseRules.accountToken(value, expectedProduct: productID))
    }
    private func submit(_ proof: AppleTransactionProof, householdID: String, intent: ApplePurchaseIntent?, context: String) async throws {
        guard context == purchaseContext else { throw CancellationError() }
        guard appleDestinationAllowed else { throw FrameError.message("Reconnect to the configured hosted service before verifying your purchase.") }
        var body: [String: Any] = ["signed_transaction": proof.signedTransaction]
        if let intent { body["checkout_reference"] = intent.checkoutReference }
        let result: ReleaseEntitlement = try await connection.json("api/v1/households/\(householdID)/billing/apple/transactions", method: "POST", body: body)
        entitlement = result
        await purchases.finish(proof)
        purchaseMessage = result.active ? "Your subscription is active." : "Apple’s transaction was verified. Current subscription status: \(result.status)."
        try await connection.refreshIdentity()
    }
    private func purchaseWithApple(productID: String) async {
        let context = purchaseContext
        busy = true; defer { busy = false }
        do {
            let (hid, value, token) = try await intent(productID: productID)
            if let proof = try await purchases.purchase(productID: productID, accountToken: token) { try await submit(proof, householdID: hid, intent: value, context: context) }
            else { purchaseMessage = "Your purchase is pending Apple approval. Service access is enabled only after verification." }
            failure = nil
        } catch is CancellationError { purchaseMessage = "Purchase canceled." }
        catch { failure = error.localizedDescription }
    }
    private func restoreAccount(productID: String) async throws -> (String, UUID) {
        guard appleDestinationAllowed, let hid = household?.id ?? createdHouseholdID else { throw FrameError.message("Choose the existing hosted household that owns this App Store subscription before restoring.") }
        struct Account: Decodable { let appAccountToken: String; let productIds: [String] }
        let account: Account = try await connection.json("api/v1/households/\(hid)/billing/apple/account")
        guard let token = UUID(uuidString: account.appAccountToken), account.productIds.contains(productID) else { throw FrameError.message("This household has no matching App Store purchase account.") }
        return (hid, token)
    }
    private func restoreApple(productID: String) async {
        let context = purchaseContext
        busy = true; defer { busy = false }
        do {
            let (hid, token) = try await restoreAccount(productID: productID)
            let results = try await purchases.restore(productIDs: [productID], accountToken: token)
            if results.isEmpty { purchaseMessage = "No active Apple subscription belongs to this household. Purchases cannot be moved between households by restoring." }
            for proof in results { try await submit(proof, householdID: hid, intent: nil, context: context) }
            failure = nil
        } catch is CancellationError { }
        catch { failure = error.localizedDescription }
    }
    private func finishPendingApple(productID: String) async {
        let context = purchaseContext
        busy = true; defer { busy = false }
        do {
            let (hid, token) = try await restoreAccount(productID: productID)
            let results = purchases.matchingPending(productID: productID, accountToken: token)
            if results.isEmpty { purchaseMessage = "No pending purchase belongs to this household and plan." }
            for proof in results { try await submit(proof, householdID: hid, intent: nil, context: context) }
            failure = nil
        } catch { failure = error.localizedDescription }
    }
    private func refresh() async {
        do {
            if let hid = household?.id ?? createdHouseholdID {
                entitlement = try await connection.json("api/v1/households/\(hid)/billing/entitlement")
                if entitlement?.active == true { try await connection.refreshIdentity() }
            }
        } catch { failure = error.localizedDescription }
    }
}

struct DeviceSessionsView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var sessions: [ReleaseDeviceSession] = []
    @State private var failure: String?
    @State private var selected: ReleaseDeviceSession?
    var body: some View {
        List {
            ForEach(sessions.filter { !$0.revoked }) { device in
                HStack {
                    VStack(alignment: .leading) {
                        Text(device.deviceName)
                        if device.id == connection.identity?.sessionId { Text("This device").font(.caption).foregroundStyle(.secondary) }
                    }
                    Spacer()
                    Button("Revoke", role: .destructive) { selected = device }
                }
            }
            if let failure { Text(failure).foregroundStyle(.secondary) }
        }
        .navigationTitle("Signed-In Devices")
        .task { await refresh() }
        .refreshable { await refresh() }
        .confirmationDialog("Sign out \(selected?.deviceName ?? "this device")?", isPresented: Binding(get: { selected != nil }, set: { if !$0 { selected = nil } }), titleVisibility: .visible) {
            if let device = selected { Button("Revoke Device Sign-In", role: .destructive) { Task { await revoke(device) } } }
        }
    }
    private func refresh() async {
        do {
            struct Sessions: Decodable { let sessions: [ReleaseDeviceSession] }
            let result: Sessions = try await connection.json("api/v1/auth/sessions")
            sessions = result.sessions; failure = nil
        } catch { failure = error.localizedDescription }
    }
    private func revoke(_ device: ReleaseDeviceSession) async {
        do {
            let _: ReleaseAcknowledgement = try await connection.json("api/v1/auth/sessions/\(device.id)", method: "DELETE")
            if device.id == connection.identity?.sessionId { await connection.signOut() }
            else { await refresh() }
        } catch { failure = error.localizedDescription }
    }
}
