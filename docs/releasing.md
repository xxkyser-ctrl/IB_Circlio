# Publishing a release

1. Bump `VERSION` in `version.py`; the Windows build synchronizes both browser manifests from it.
2. Add the matching `vX.Y.Z` section to `RELEASE_NOTES.md`.
3. Run `py -3 -B -m unittest -q` and `node --test test_extension_updates.js`.
4. Commit and push the changes to `main`.
5. Create and push the matching tag:

   ```powershell
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

6. Confirm the release workflow succeeds, the GitHub Release is published, and the Windows ZIP, Chromium extension ZIP, Firefox extension ZIP, and `SHA256SUMS.txt` are attached.

Users with update checks enabled are notified after GitHub's latest-release API reports the published release.
