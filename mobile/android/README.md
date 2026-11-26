# OASX Android

The mobile app bundles the Flutter interface, the OAS Python scheduler and task modules, and an Android bridge for the game. The app uses a loopback API for local control. Background play requires a working Shizuku connection and a verified virtual display for the selected client.

## Build

Use JDK 17, the Android SDK with API 36, and the Flutter SDK recorded by the project. Local Gradle and Python settings are in the Android project. The default APK version comes from `pubspec.yaml`.

```powershell
flutter build apk --release --target-platform android-x64
flutter build apk --release --target-platform android-arm64
```

Copy the first artifact before building the second architecture. Use the installed app's signing key to verify an upgrade. Keep signing material and passphrases outside the repository.

## Verify

Use the dedicated MuMu test instance. Check APK installation, the reported build version, the task catalog, and the original plan before a game run. Verify that the game stays on its virtual display when the control interface leaves the foreground. Exercise configured-role and default-role login, identity confirmation, scheduler stop and resume, and actual task results. Flutter and host test commands cover the software contracts alongside those runs.
