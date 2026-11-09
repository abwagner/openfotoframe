# Internal TestFlight builds

This configuration distributes only to App Store Connect internal testers. It
cannot be used for external TestFlight testing or an App Store release.

## Prepare

1. Use an active Apple Developer Program team and an App Store Connect account
   with permission to upload builds. Sign in through Xcode Settings > Accounts.
2. Set `OPENFOTOFRAME_DEVELOPMENT_TEAM` in ignored `ios/Signing.local.xcconfig`.
   Keep signing keys, account credentials, and local configuration out of Git.
3. Set `MARKETING_VERSION` and an unused `CURRENT_PROJECT_VERSION` in
   `ios/project.yml`, then run `xcodegen generate --spec ios/project.yml`.
4. Run the unit and isolated integration checks described in `ios/README.md`.
   Restart the fixture server before a complete integration run.
5. Create the App Store Connect iOS app record for bundle ID
   `tech.swagner.openfotoframe` if it does not already exist. Check that its name,
   SKU, primary language, and team are correct.

## Archive and export

Run from `ios/`, substituting a destination outside the repository:

```sh
xcodebuild -project OpenFotoFrame.xcodeproj -scheme OpenFotoFrame \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath /private/tmp/OpenFotoFrame.xcarchive \
  -allowProvisioningUpdates archive

xcodebuild -exportArchive \
  -archivePath /private/tmp/OpenFotoFrame.xcarchive \
  -exportPath /private/tmp/OpenFotoFrame-export \
  -exportOptionsPlist distribution/ExportOptions-Internal.plist \
  -allowProvisioningUpdates
```

Export requires an Apple Distribution signing identity and App Store provisioning
profile, or automatic signing access that can obtain them. A successful
development-signed archive alone does not establish distribution readiness.

If command-line automatic signing reports `No Accounts` despite an account in
Xcode Settings, use Organizer with that account, or export with a private manual
options file. For manual export, set `signingStyle` to `manual`, `teamID` to the
developer team, `signingCertificate` to `Apple Distribution`, and
`provisioningProfiles` to a dictionary mapping the app's bundle ID to its App Store
profile UUID. Install the matching certificate/private key in Keychain and the
profile under `~/Library/Developer/Xcode/UserData/Provisioning Profiles/`.
Keep this machine-specific options file outside the repository. Do not revoke
existing certificates to resolve a local signing problem.

The export options preserve the specified version/build number and mark the
build internal-only. Upload the exported IPA using Apple's Transporter, or use
Xcode Organizer's TestFlight Internal Only distribution workflow. For an
authenticated command-line upload, make a temporary copy of the export options
with `destination` set to `upload` and export the same archive using that copy.

## Enable testing

1. Wait for the build to finish processing in App Store Connect > TestFlight.
2. Resolve any export-compliance warning. This app currently uses only operating
   system encryption through URLSession/WebKit and declares
   `ITSAppUsesNonExemptEncryption = NO`. Reassess if dependencies or cryptography
   change.
3. Assign the build to an internal testing group and select only the intended
   App Store Connect team users. Internal testing does not use a public link.
4. Install through TestFlight on a physical device. Verify first launch, server
   connection, login/logout, uploads, mat previews, native/web session sharing,
   and upgrading an existing installation without losing its saved server.

New installations ask for a server URL; existing installations retain their
saved server. The hosted registration, billing, invite/QR onboarding, and new
first-party authentication architecture are separate release work and are not
implemented by this distribution setup.

Sources: [Apple upload guide](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/),
[internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers/),
[encryption declaration](https://developer.apple.com/documentation/bundleresources/information-property-list/itsappusesnonexemptencryption).
