# Luma development

Read docs/REQUIREMENTS.md on each iteration and continue the remaining work.

Use the native C# / XAML WinUI 3 frontend. Keep the Python engine Qt-free at runtime.
Use isolated synthetic profiles for development. Never open a user library, image,
prompt, model weight or credential while testing. Store temporary/build output
inside this repository. Preserve real Data and original files during upgrades.

Use the existing control styles: rectangular buttons, rounded input fields,
consistent row heights, native keyboard and selection behavior. Dropdown layout
must be ready before its first frame. File moves and duplicate cleanup require
a concrete persisted review and explicit confirmation. Keep indexes paginated.

Build and release instructions are in docs/BUILD.md. Only publish reviewed source
and audited delivery files. Do not commit Data, credentials, generated artifacts
or private diagnostics.
