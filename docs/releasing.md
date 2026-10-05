# Publishing a release

1. Bump `VERSION` in `version.py` and update the version in both root browser manifests. The unit tests fail if these values or the release-notes heading disagree.
2. Add the matching `vX.Y.Z` section to `RELEASE_NOTES.md`.
3. Run `py -3 -B -m unittest -q`, `node --test test_extension_updates.js`, and `py -3 -m pip_audit -r requirements-build.txt`.
4. Build with `py -3 -B build_release.py`; verify all three packages, local-data exclusions, and `SHA256SUMS.txt`. Use disposable data for encryption-migration and packaged-service smoke tests.
5. Commit and push the changes to `main`, then wait for the CI workflow to pass.
6. Create and push the matching tag:

   ```powershell
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

7. Confirm the release workflow succeeds, the GitHub Release is published, and the Windows ZIP, Chromium extension ZIP, Firefox extension ZIP, and `SHA256SUMS.txt` are attached. Verify the published checksums.

Users with update checks enabled are notified after GitHub's latest-release API reports the published release.
