#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The desktop plugin's packages (docs/PLUGIN.md "Packages"), under the fixed names the release attaches:
# The bundles first: make plugin (macOS), tools/plugin_win.sh build (Windows, clang-cl), tools/plugin_linux.sh build
# (Linux, Docker); those scripts' pack step calls this one.
#   tools/plugin_pack.sh macos   [BUILD] [OUT]    OUT/frodhes-plugin-macos.pkg and frodhes-plugin-macos.zip
#   tools/plugin_pack.sh windows [BUILD] [OUT]    OUT/frodhes-plugin-windows.zip
#   tools/plugin_pack.sh linux   [BUILD] [OUT]    OUT/frodhes-plugin-linux-<x86_64|aarch64>.zip (the
#                                                 architecture of the bundle in BUILD)
# BUILD defaults to build/plugin (macOS), build/plugin-win, build/plugin-linux; OUT to build/plugin-pkg.
# Every package carries the GPL (LICENSE.txt), the source offer and THIRD-PARTY-NOTICES.txt (the licence texts of
# what the binaries contain, taken from the pinned checkouts); the macOS and Windows VST3 bundles and the macOS
# bundles carry the same two files in Contents/Resources.
#
# macOS signing. Without the variables below every bundle is signed ad hoc (codesign -s -) and the .pkg is unsigned:
# Gatekeeper then asks the user to allow the installer (System Settings > Privacy & Security > Open Anyway), and a
# copy from the zip needs `xattr -dr com.apple.quarantine`. With an Apple Developer ID (docs/PLUGIN.md "Signing"):
#   FRP_SIGN_ID="Developer ID Application: NAME (TEAMID)"    the bundles: hardened runtime + secure timestamp
#   FRP_INSTALLER_ID="Developer ID Installer: NAME (TEAMID)" the .pkg (productbuild --sign)
#   FRP_NOTARY_PROFILE=<profile>      xcrun notarytool store-credentials' keychain profile: the .pkg and the zip's
#                                     bundles are notarized and stapled
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPS=${FRP_DEPS_DIR:-"$ROOT/.local/plugin-deps"}
ID=io.github.leonardomirabal.frodhes

die() { echo "plugin_pack: $*" >&2; exit 1; }

notices() {   # DIR [windows]: LICENSE.txt, SOURCE.txt, THIRD-PARTY-NOTICES.txt
    mkdir -p "$1"
    cp "$ROOT/LICENSE" "$1/LICENSE.txt"
    cat > "$1/SOURCE.txt" <<EOF
Frodhes is free software under the GNU General Public License, version 3 only (LICENSE.txt).
Its complete source, the plugin's build files and the exact versions of every dependency (plugin/deps.lock) are at
https://github.com/leonardoMirabal/frodhes (the release's tag). The plugin is built from plugin/ (docs/PLUGIN.md).
EOF
    n=$1/THIRD-PARTY-NOTICES.txt
    {
        echo "Third-party material in the Frodhes plugin binaries"
        echo "==================================================="
        echo
        echo "Frodhes itself: GPL-3.0-only (LICENSE.txt). The binaries also contain the following, each under its"
        echo "own licence, reproduced below from the exact versions built (plugin/deps.lock)."
        echo
        echo "  CLAP (the plugin API headers)                 MIT         https://github.com/free-audio/clap"
        echo "  clap-wrapper (the VST3 and Audio Unit layers)  MIT         https://github.com/free-audio/clap-wrapper"
        echo "    with its copy of {fmt}                       MIT         https://github.com/fmtlib/fmt"
        echo "    and the PreSonus gain-reduction header       public domain"
        echo "  VST3 SDK                                       MIT         https://github.com/steinbergmedia/vst3sdk"
        [ "${2:-}" = macos ] &&
        echo "  AudioUnitSDK (the Audio Unit only)             Apache-2.0  https://github.com/apple/AudioUnitSDK"
        [ "${2:-}" = windows ] &&
        echo "  The Microsoft C/C++ runtime, linked statically  Microsoft's redistribution terms (no notice needed)"
        [ "${2:-}" = linux ] &&
        echo "  libstdc++ and libgcc (GCC runtime)             GPL-3.0 WITH GCC-exception-3.1"
        echo "  Inter Tight and Michroma (the window's fonts)  SIL OFL 1.1 (texts below)"
        echo
        echo "VST is a trademark of Steinberg Media Technologies GmbH. Audio Units is a trademark of Apple Inc."
        echo "Frodhes is not made or endorsed by either company."
        sect() { echo; echo "------------------------------------------------------------------------------"; echo "$1";
                 echo "------------------------------------------------------------------------------"; echo; cat "$2"; }
        sect "CLAP" "$DEPS/clap/LICENSE"
        sect "clap-wrapper" "$DEPS/clap-wrapper/LICENSE"
        sect "{fmt} (clap-wrapper/libs/fmt; its licence, from format.h)" /dev/stdin <<'FMT'
Copyright (c) 2012 - present, Victor Zverovich and {fmt} contributors

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

--- Optional exception to the license ---

As an exception, if, as a result of your compiling your source code, portions
of this Software are embedded into a machine-executable object form of such
source code, you may redistribute such embedded portions in such object form
without including the above copyright and permission notices.
FMT
        sect "VST3 SDK" "$DEPS/vst3sdk/LICENSE.txt"
        sect "Inter Tight (the window's text)" "$ROOT/assets/fonts/OFL-InterTight.txt"
        sect "Michroma (the window's wordmark and titles)" "$ROOT/assets/fonts/OFL-Michroma.txt"
        if [ "${2:-}" = macos ]; then sect "AudioUnitSDK" "$DEPS/audiounitsdk/LICENSE.txt"; fi
    } > "$n"
    grep -q "MIT License" "$n" || die "notices: a licence text is missing"
}

macos() {
    B=${1:-$ROOT/build/plugin}/bundles; O=${2:-$ROOT/build/plugin-pkg}
    [ -d "$B/Frodhes.component" ] || die "no bundles in $B (make plugin)"
    S=$O/_mac; rm -rf "$S"; mkdir -p "$S/Frodhes" "$O"
    notices "$S/Frodhes" macos
    V=$(sed -n 's/^FRP_VERSION:STRING=//p' "$B/../CMakeCache.txt"); V=${V:-0.0.0}
    for x in CLAP:clap VST3:vst3 Components:component; do
        d=${x%%:*}; e=${x#*:}
        mkdir -p "$S/Frodhes/$d"
        ditto "$B/Frodhes.$e" "$S/Frodhes/$d/Frodhes.$e"
        mkdir -p "$S/Frodhes/$d/Frodhes.$e/Contents/Resources"
        cp "$S/Frodhes/LICENSE.txt" "$S/Frodhes/THIRD-PARTY-NOTICES.txt" "$S/Frodhes/$d/Frodhes.$e/Contents/Resources/"
        lipo -archs "$S/Frodhes/$d/Frodhes.$e/Contents/MacOS/Frodhes" | grep -q "x86_64 arm64" ||
            die "Frodhes.$e is not universal (arm64 + x86_64)"
        if [ -n "${FRP_SIGN_ID:-}" ]; then
            codesign --force --options runtime --timestamp --sign "$FRP_SIGN_ID" "$S/Frodhes/$d/Frodhes.$e"
        else
            codesign --force --sign - "$S/Frodhes/$d/Frodhes.$e"
        fi
        codesign --verify --strict "$S/Frodhes/$d/Frodhes.$e"
    done
    cat > "$S/Frodhes/INSTALL.txt" <<'EOF'
Frodhes plugin for macOS 11 or newer (Apple silicon and Intel)

The installer (frodhes-plugin-macos.pkg) puts the three formats into your own Library, no administrator password:
  ~/Library/Audio/Plug-Ins/CLAP/Frodhes.clap
  ~/Library/Audio/Plug-Ins/VST3/Frodhes.vst3
  ~/Library/Audio/Plug-Ins/Components/Frodhes.component     (Audio Unit)

By hand from this zip: copy each bundle into the folder of the same name above, then clear the download flag:
  xattr -dr com.apple.quarantine ~/Library/Audio/Plug-Ins/*/Frodhes.*
  killall -9 AudioComponentRegistrar      (makes the Audio Unit visible at once)
and rescan in your DAW. To remove the plugin, delete the three bundles.
EOF
    rm -f "$O/frodhes-plugin-macos.zip"
    (cd "$S" && ditto -c -k --sequesterRsrc --keepParent Frodhes "$O/frodhes-plugin-macos.zip")

    # the installer: one component package a format (not relocatable: Installer must not "update" a copy of the
    # bundle it finds elsewhere), a distribution that installs into the user's home only
    P=$S/pkg; mkdir -p "$P/res" "$P/au-scripts"
    printf '#!/bin/sh\nkillall -9 AudioComponentRegistrar >/dev/null 2>&1\nexit 0\n' > "$P/au-scripts/postinstall"
    chmod +x "$P/au-scripts/postinstall"
    for x in CLAP:clap VST3:vst3 Components:component; do
        d=${x%%:*}; e=${x#*:}
        mkdir -p "$P/root-$e"; ditto "$S/Frodhes/$d/Frodhes.$e" "$P/root-$e/Frodhes.$e"
        pkgbuild --analyze --root "$P/root-$e" "$P/$e.plist" >/dev/null
        plutil -replace 0.BundleIsRelocatable -bool NO "$P/$e.plist"
        set -- --root "$P/root-$e" --component-plist "$P/$e.plist" --identifier "$ID.$e" --version "$V" \
            --install-location "/Library/Audio/Plug-Ins/$d"
        [ "$e" = component ] && set -- "$@" --scripts "$P/au-scripts"
        pkgbuild "$@" "$P/$e.pkg" >/dev/null
    done
    cp "$S/Frodhes/LICENSE.txt" "$P/res/LICENSE.txt"
    cp "$S/Frodhes/INSTALL.txt" "$P/res/README.txt"
    cat > "$P/dist.xml" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>Frodhes</title>
    <license file="LICENSE.txt"/>
    <readme file="README.txt"/>
    <domains enable_anywhere="false" enable_currentUserHome="true" enable_localSystem="false"/>
    <options customize="allow" require-scripts="false" hostArchitectures="arm64,x86_64"/>
    <os-version min="11.0"/>
    <choices-outline>
        <line choice="clap"/>
        <line choice="vst3"/>
        <line choice="component"/>
    </choices-outline>
    <choice id="clap" title="CLAP" description="Frodhes.clap into ~/Library/Audio/Plug-Ins/CLAP"><pkg-ref id="$ID.clap"/></choice>
    <choice id="vst3" title="VST3" description="Frodhes.vst3 into ~/Library/Audio/Plug-Ins/VST3"><pkg-ref id="$ID.vst3"/></choice>
    <choice id="component" title="Audio Unit" description="Frodhes.component into ~/Library/Audio/Plug-Ins/Components"><pkg-ref id="$ID.component"/></choice>
    <pkg-ref id="$ID.clap" version="$V" onConclusion="none">clap.pkg</pkg-ref>
    <pkg-ref id="$ID.vst3" version="$V" onConclusion="none">vst3.pkg</pkg-ref>
    <pkg-ref id="$ID.component" version="$V" onConclusion="none">component.pkg</pkg-ref>
</installer-gui-script>
EOF
    set -- --distribution "$P/dist.xml" --resources "$P/res" --package-path "$P"
    [ -n "${FRP_INSTALLER_ID:-}" ] && set -- "$@" --sign "$FRP_INSTALLER_ID" --timestamp
    rm -f "$O/frodhes-plugin-macos.pkg"
    productbuild "$@" "$O/frodhes-plugin-macos.pkg" >/dev/null

    if [ -n "${FRP_NOTARY_PROFILE:-}" ]; then   # (needs FRP_SIGN_ID and FRP_INSTALLER_ID: Apple refuses ad-hoc code)
        [ -n "${FRP_SIGN_ID:-}" ] && [ -n "${FRP_INSTALLER_ID:-}" ] || die "notarizing needs both signing identities"
        xcrun notarytool submit "$O/frodhes-plugin-macos.pkg" --keychain-profile "$FRP_NOTARY_PROFILE" --wait
        xcrun stapler staple "$O/frodhes-plugin-macos.pkg"
        xcrun notarytool submit "$O/frodhes-plugin-macos.zip" --keychain-profile "$FRP_NOTARY_PROFILE" --wait
        for x in CLAP/Frodhes.clap VST3/Frodhes.vst3 Components/Frodhes.component; do
            xcrun stapler staple "$S/Frodhes/$x"
        done
        rm -f "$O/frodhes-plugin-macos.zip"
        (cd "$S" && ditto -c -k --sequesterRsrc --keepParent Frodhes "$O/frodhes-plugin-macos.zip")
    fi
    rm -rf "$S"
    ls -l "$O/frodhes-plugin-macos.pkg" "$O/frodhes-plugin-macos.zip"
}

windows() {
    B=${1:-$ROOT/build/plugin-win}/bundles; O=${2:-$ROOT/build/plugin-pkg}
    [ -f "$B/CLAP/Frodhes.clap" ] && [ -f "$B/VST3/Frodhes.vst3/Contents/x86_64-win/Frodhes.vst3" ] ||
        die "no Windows bundles in $B (tools/plugin_win.sh build)"
    S=$O/_win; rm -rf "$S"; mkdir -p "$S/Frodhes/CLAP" "$S/Frodhes/VST3" "$O"
    notices "$S/Frodhes" windows
    cp "$B/CLAP/Frodhes.clap" "$S/Frodhes/CLAP/"
    mkdir -p "$S/Frodhes/VST3/Frodhes.vst3/Contents/x86_64-win" "$S/Frodhes/VST3/Frodhes.vst3/Contents/Resources"
    cp "$B/VST3/Frodhes.vst3/Contents/x86_64-win/Frodhes.vst3" "$S/Frodhes/VST3/Frodhes.vst3/Contents/x86_64-win/"
    cp "$S/Frodhes/LICENSE.txt" "$S/Frodhes/THIRD-PARTY-NOTICES.txt" "$S/Frodhes/VST3/Frodhes.vst3/Contents/Resources/"
    cat > "$S/Frodhes/INSTALL.txt" <<'EOF'
Frodhes plugin for Windows 10 or newer (x64)

Copy the formats your DAW uses (no installer, nothing else needed):
  CLAP\Frodhes.clap    into  C:\Program Files\Common Files\CLAP\
                       (or, for you only: %LOCALAPPDATA%\Programs\Common\CLAP\)
  VST3\Frodhes.vst3    the whole folder, into  C:\Program Files\Common Files\VST3\
Then rescan in your DAW. To remove the plugin, delete what you copied.
The files are not code-signed: if Windows marks them as downloaded, right-click each, Properties, Unblock.
EOF
    rm -f "$O/frodhes-plugin-windows.zip"
    (cd "$S" && cmake -E tar cf "$O/frodhes-plugin-windows.zip" --format=zip Frodhes)
    rm -rf "$S"
    ls -l "$O/frodhes-plugin-windows.zip"
}

linux() {
    B=${1:-$ROOT/build/plugin-linux}/bundles; O=${2:-$ROOT/build/plugin-pkg}
    A=""
    for a in x86_64 aarch64; do if [ -f "$B/Frodhes.vst3/Contents/$a-linux/Frodhes.so" ]; then A=$a; fi; done
    [ -f "$B/Frodhes.clap" ] && [ -n "$A" ] || die "no Linux bundles in $B (tools/plugin_linux.sh build)"
    S=$O/_linux; rm -rf "$S"; mkdir -p "$S/Frodhes" "$O"
    notices "$S/Frodhes" linux
    cp "$B/Frodhes.clap" "$S/Frodhes/"
    cp -R "$B/Frodhes.vst3" "$S/Frodhes/"
    cat > "$S/Frodhes/INSTALL.txt" <<'EOF'
Frodhes plugin for Linux (glibc 2.31 or newer: Ubuntu 20.04, Debian 11, Fedora 32 and later)

  cp Frodhes.clap ~/.clap/            (or /usr/lib/clap/ for everyone)
  cp -R Frodhes.vst3 ~/.vst3/         (or /usr/lib/vst3/ for everyone)
Then rescan in your DAW. To remove the plugin, delete what you copied.
EOF
    rm -f "$O/frodhes-plugin-linux-$A.zip"
    (cd "$S" && cmake -E tar cf "$O/frodhes-plugin-linux-$A.zip" --format=zip Frodhes)
    rm -rf "$S"
    ls -l "$O/frodhes-plugin-linux-$A.zip"
}

case ${1:-} in
macos) shift; macos "$@" ;;
windows) shift; windows "$@" ;;
linux) shift; linux "$@" ;;
*) sed -n '3,10p' "$0"; exit 2 ;;
esac
