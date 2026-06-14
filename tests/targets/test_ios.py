import os.path
import plistlib
import sys
import tempfile
from unittest import mock

import pytest

from buildozer.buildops import CommandResult
from buildozer.exceptions import BuildozerCommandException
from buildozer.targets.ios import TargetIos
from tests.targets.utils import (
    init_buildozer,
    patch_buildops_checkbin,
    patch_buildops_cmd,
    patch_buildops_file_exists,
    patch_logger_error,
)


def patch_target_ios(method):
    return mock.patch("buildozer.targets.ios.TargetIos.{method}".format(method=method))


def init_target(temp_dir, options=None):
    buildozer = init_buildozer(temp_dir, "ios", options)
    return TargetIos(buildozer)


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Only macOS is supported for target iOS"
)
class TestTargetIos:
    def setup_method(self):
        """
        Create a temporary directory that will contain the spec file and will
        serve as the root_dir.
        """
        self.temp_dir = tempfile.TemporaryDirectory()

    def tear_method(self):
        """
        Remove the temporary directory created in self.setup_method.
        """
        self.temp_dir.cleanup()

    def test_init(self):
        """Tests init defaults."""
        target = init_target(self.temp_dir)
        assert target.targetname == "ios"
        assert target.code_signing_allowed == "CODE_SIGNING_ALLOWED=NO"
        assert target.build_mode == "debug"
        assert target.platform_update is False

    def test_check_requirements(self):
        """Basic tests for the check_requirements() method."""
        target = init_target(self.temp_dir)
        assert not hasattr(target, "javac_cmd")
        with patch_buildops_checkbin() as m_checkbin:
            target.check_requirements()
        assert m_checkbin.call_args_list == [
            mock.call("Xcode xcodebuild", "xcodebuild"),
            mock.call("Xcode xcode-select", "xcode-select"),
            mock.call("Git git", "git"),
            mock.call("Cython cython", "cython"),
            mock.call("pkg-config", "pkg-config"),
            mock.call("autoconf", "autoconf"),
            mock.call("automake", "automake"),
            mock.call("libtool", "libtool"),
        ]
        assert target._toolchain_cmd[-1] == "toolchain.py"
        assert target._xcodebuild_cmd == ["xcodebuild"]

    def test_check_configuration_tokens(self):
        """Basic tests for the check_configuration_tokens() method."""
        target = init_target(self.temp_dir, {"ios.codesign.allowed": "yes"})
        with mock.patch(
            "buildozer.targets.android.Target.check_configuration_tokens"
        ) as m_check_configuration_tokens, mock.patch(
            "buildozer.targets.ios.TargetIos._get_available_identities"
        ) as m_get_available_identities:
            target.check_configuration_tokens()
        assert m_get_available_identities.call_args_list == [mock.call()]
        assert m_check_configuration_tokens.call_args_list == [
            mock.call(
                [
                    '[app] "ios.codesign.debug" key missing, you must give a certificate name to use.',
                    '[app] "ios.codesign.release" key missing, you must give a certificate name to use.',
                ]
            )
        ]

    def test_get_available_packages(self):
        """Checks the toolchain `recipes --compact` output is parsed correctly to return recipe list."""
        target = init_target(self.temp_dir)
        with patch_target_ios("toolchain") as m_toolchain:
            m_toolchain.return_value = ("hostpython3 kivy pillow python3 sdl2", None, 0)
            available_packages = target.get_available_packages()
        assert m_toolchain.call_args_list == [
            mock.call(["recipes", "--compact"], get_stdout=True)
        ]
        assert available_packages == [
            "hostpython3",
            "kivy",
            "pillow",
            "python3",
            "sdl2",
        ]

    def test_install_platform(self):
        """Checks `install_platform()` calls clone commands and sets `ios_dir` and `ios_deploy_dir` attributes."""
        target = init_target(self.temp_dir)
        assert target.ios_dir is None
        assert target.ios_deploy_dir is None
        with patch_buildops_cmd() as m_cmd:
            target.install_platform()
        assert m_cmd.call_args_list == [
            mock.call(
                [
                    "git",
                    "clone",
                    "--branch",
                    "master",
                    "https://github.com/kivy/kivy-ios",
                ],
                cwd=mock.ANY,
                env=mock.ANY,
            ),
            mock.call(
                [
                    "git",
                    "clone",
                    "--branch",
                    "1.12.2",
                    "https://github.com/phonegap/ios-deploy",
                ],
                cwd=mock.ANY,
                env=mock.ANY,
            ),
        ]
        assert target.ios_dir.endswith(".buildozer/ios/platform/kivy-ios")
        assert target.ios_deploy_dir.endswith(".buildozer/ios/platform/ios-deploy")

    def test_compile_platform(self):
        """Checks the `toolchain build` command is called on the ios requirements."""
        target = init_target(self.temp_dir)
        target.ios_deploy_dir = "/ios/deploy/dir"
        # fmt: off
        with patch_target_ios("get_available_packages") as m_get_available_packages, \
             patch_target_ios("toolchain") as m_toolchain, \
             patch_buildops_file_exists() as m_file_exists:
            m_get_available_packages.return_value = ["hostpython3", "python3"]
            m_file_exists.return_value = True
            target.compile_platform()
        # fmt: on
        assert m_get_available_packages.call_args_list == [mock.call()]
        assert m_toolchain.call_args_list == [mock.call(["build", "python3"])]
        assert m_file_exists.call_args_list == [
            mock.call(os.path.join(target.ios_deploy_dir, "ios-deploy"))
        ]

    def test_get_package(self):
        """Checks default package values and checks it can be overridden."""
        # default value
        target = init_target(self.temp_dir)
        package = target._get_package()
        assert package == "org.test.myapp"
        # override
        target = init_target(
            self.temp_dir,
            {"package.domain": "com.github.kivy", "package.name": "buildozer"},
        )
        package = target._get_package()
        assert package == "com.github.kivy.buildozer"

    def test_unlock_keychain_wrong_password(self):
        """A `BuildozerCommandException` should be raised on wrong password 3 times."""
        target = init_target(self.temp_dir)
        # fmt: off
        with mock.patch("buildozer.targets.ios.getpass") as m_getpass, \
             patch_buildops_cmd() as m_cmd, \
             pytest.raises(BuildozerCommandException):
            m_getpass.return_value = "password"
            # the `security unlock-keychain` command returned an error
            # hence we'll get prompted to enter the password
            m_cmd.return_value = CommandResult(None, None, 123)
            target._unlock_keychain()
        # fmt: on
        assert m_getpass.call_args_list == [
            mock.call("Password to unlock the default keychain:"),
            mock.call("Password to unlock the default keychain:"),
            mock.call("Password to unlock the default keychain:"),
        ]


class TestTargetIosSigning:
    """Signing / export / OTA helpers.

    These do not invoke a real xcodebuild and therefore run on every platform,
    unlike the macOS-only ``TestTargetIos`` suite above. They cover the matrix
    of (signing allowed) x (style) x (identity/profile present).
    """

    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def teardown_method(self):
        self.temp_dir.cleanup()

    # -- _get_signing_build_settings -------------------------------------

    def test_signing_build_settings_unsigned_is_empty(self):
        """allowed=false -> no signing settings at all (unsigned build)."""
        target = init_target(self.temp_dir)  # allowed=false by default
        assert target._get_signing_build_settings() == []

    def test_signing_build_settings_unsigned_ignores_signing_config(self):
        """allowed=false -> even manual tokens are ignored, still empty."""
        target = init_target(
            self.temp_dir,
            {
                "ios.codesign.style.debug": "manual",
                "ios.codesign.debug": '"iPhone Distribution: ACME (TEAM123)"',
                "ios.codesign.development_team.debug": "TEAM123",
                "ios.codesign.provisioning_profile.debug": "MyProfile",
            },
        )
        assert target._get_signing_build_settings() == []

    def test_signing_build_settings_automatic(self):
        """allowed=true + automatic -> only CODE_SIGN_STYLE=Automatic."""
        target = init_target(self.temp_dir, {"ios.codesign.allowed": "true"})
        assert target._get_signing_build_settings() == ["CODE_SIGN_STYLE=Automatic"]

    def test_signing_build_settings_automatic_with_team(self):
        target = init_target(
            self.temp_dir,
            {
                "ios.codesign.allowed": "true",
                "ios.codesign.development_team.debug": "TEAM123",
            },
        )
        assert target._get_signing_build_settings() == [
            "DEVELOPMENT_TEAM=TEAM123",
            "CODE_SIGN_STYLE=Automatic",
        ]

    def test_signing_build_settings_manual(self):
        """allowed=true + manual -> style + identity + profile (+ team)."""
        target = init_target(
            self.temp_dir,
            {
                "ios.codesign.allowed": "true",
                "ios.codesign.style.debug": "manual",
                "ios.codesign.debug": '"iPhone Distribution: ACME (TEAM123)"',
                "ios.codesign.development_team.debug": "TEAM123",
                "ios.codesign.provisioning_profile.debug": "MyProfile",
            },
        )
        assert target._get_signing_build_settings() == [
            "DEVELOPMENT_TEAM=TEAM123",
            "CODE_SIGN_STYLE=Manual",
            "CODE_SIGN_IDENTITY=iPhone Distribution: ACME (TEAM123)",
            "PROVISIONING_PROFILE_SPECIFIER=MyProfile",
        ]

    def test_signing_build_settings_automatic_ignores_identity_and_profile(self):
        """automatic must ignore a configured identity/profile; only the team
        and the style are passed (never CODE_SIGN_IDENTITY /
        PROVISIONING_PROFILE_SPECIFIER)."""
        target = init_target(
            self.temp_dir,
            {
                "ios.codesign.allowed": "true",
                # style.debug defaults to "automatic"
                "ios.codesign.development_team.debug": "TEAM123",
                "ios.codesign.debug": '"iPhone Distribution: ACME (TEAM123)"',
                "ios.codesign.provisioning_profile.debug": "MyProfile",
            },
        )
        assert target._get_signing_build_settings() == [
            "DEVELOPMENT_TEAM=TEAM123",
            "CODE_SIGN_STYLE=Automatic",
        ]

    # -- code_signing_style ----------------------------------------------

    def test_invalid_signing_style_falls_back_to_automatic(self):
        target = init_target(self.temp_dir, {"ios.codesign.style.debug": "nonsense"})
        with patch_logger_error() as m_error:
            assert target.code_signing_style == "automatic"
        assert m_error.call_count == 1

    # -- _validate_export_signing ----------------------------------------

    def test_validate_signing_skipped_when_disabled(self):
        """allowed=false -> validation is a no-op, even for manual + missing."""
        target = init_target(self.temp_dir, {"ios.codesign.style.debug": "manual"})
        with patch_logger_error() as m_error:
            assert target._validate_export_signing() is True
        assert m_error.call_args_list == []

    def test_validate_signing_automatic_requires_team(self):
        """automatic + no development team -> clear error (Xcode needs it)."""
        target = init_target(self.temp_dir, {"ios.codesign.allowed": "true"})
        with patch_logger_error() as m_error:
            assert target._validate_export_signing() is False
        assert m_error.call_args_list == [
            mock.call(
                'Automatic code signing requires a development team. '
                'You must fill the "ios.codesign.development_team.debug" token.'
            )
        ]

    def test_validate_signing_automatic_with_team(self):
        """automatic + development team -> nothing else required."""
        target = init_target(
            self.temp_dir,
            {
                "ios.codesign.allowed": "true",
                "ios.codesign.development_team.debug": "TEAM123",
            },
        )
        with patch_logger_error() as m_error:
            assert target._validate_export_signing() is True
        assert m_error.call_args_list == []

    def test_validate_signing_manual_missing_tokens(self):
        target = init_target(
            self.temp_dir,
            {"ios.codesign.allowed": "true", "ios.codesign.style.debug": "manual"},
        )
        with patch_logger_error() as m_error:
            assert target._validate_export_signing() is False
        assert m_error.call_args_list == [
            mock.call(
                'Manual code signing requires a signing certificate. '
                'You must fill the "ios.codesign.debug" token.'
            ),
            mock.call(
                'Manual code signing requires a provisioning profile. '
                'You must fill the "ios.codesign.provisioning_profile.debug" token.'
            ),
        ]

    def test_validate_signing_manual_complete(self):
        target = init_target(
            self.temp_dir,
            {
                "ios.codesign.allowed": "true",
                "ios.codesign.style.debug": "manual",
                "ios.codesign.debug": '"iPhone Distribution: ACME (TEAM123)"',
                "ios.codesign.provisioning_profile.debug": "MyProfile",
            },
        )
        with patch_logger_error() as m_error:
            assert target._validate_export_signing() is True
        assert m_error.call_args_list == []

    # -- _generate_export_options_plist ----------------------------------

    def test_generate_export_options_plist_automatic(self):
        target = init_target(
            self.temp_dir,
            {
                "ios.export_method.debug": "development",
                "ios.codesign.development_team.debug": "TEAM123",
            },
        )
        path = target._generate_export_options_plist(self.temp_dir.name)
        assert path == os.path.join(self.temp_dir.name, "ExportOptions.plist")
        with open(path, "rb") as f:
            data = plistlib.load(f)
        assert data == {
            "method": "development",
            "signingStyle": "automatic",
            "teamID": "TEAM123",
        }

    def test_generate_export_options_plist_manual(self):
        target = init_target(
            self.temp_dir,
            {
                "ios.codesign.style.debug": "manual",
                "ios.export_method.debug": "app-store",
                "ios.codesign.debug": '"iPhone Distribution: ACME (TEAM123)"',
                "ios.codesign.development_team.debug": "TEAM123",
                "ios.codesign.provisioning_profile.debug": "MyProfile",
            },
        )
        path = target._generate_export_options_plist(self.temp_dir.name)
        with open(path, "rb") as f:
            data = plistlib.load(f)
        assert data == {
            "method": "app-store",
            "signingStyle": "manual",
            "teamID": "TEAM123",
            "signingCertificate": "iPhone Distribution: ACME (TEAM123)",
            "provisioningProfiles": {"org.test.myapp": "MyProfile"},
        }

    def test_generate_export_options_plist_automatic_ignores_identity(self):
        """ExportOptions for automatic must not carry signingCertificate or
        provisioningProfiles even if an identity/profile is configured."""
        target = init_target(
            self.temp_dir,
            {
                "ios.export_method.debug": "app-store",
                # style.debug defaults to "automatic"
                "ios.codesign.development_team.debug": "TEAM123",
                "ios.codesign.debug": '"iPhone Distribution: ACME (TEAM123)"',
                "ios.codesign.provisioning_profile.debug": "MyProfile",
            },
        )
        path = target._generate_export_options_plist(self.temp_dir.name)
        with open(path, "rb") as f:
            data = plistlib.load(f)
        assert data == {
            "method": "app-store",
            "signingStyle": "automatic",
            "teamID": "TEAM123",
        }

    # -- _generate_ota_manifest ------------------------------------------

    def test_generate_ota_manifest(self):
        target = init_target(
            self.temp_dir,
            {
                "ios.manifest.app_url": "https://example.com/MyApp.ipa",
                "ios.manifest.display_image_url": "https://example.com/57.png",
                "ios.manifest.full_size_image_url": "https://example.com/512.png",
            },
        )
        os.makedirs(target.buildozer.bin_dir, exist_ok=True)
        target._generate_ota_manifest("MyApp", "1.0")
        manifest_path = os.path.join(
            target.buildozer.bin_dir, "MyApp-1.0-manifest.plist"
        )
        with open(manifest_path, "rb") as f:
            data = plistlib.load(f)
        item = data["items"][0]
        assert item["metadata"] == {
            "bundle-identifier": "org.test.myapp",
            "bundle-version": "1.0",
            "kind": "software",
            "title": "MyApp",
        }
        assert item["assets"][0] == {
            "kind": "software-package",
            "url": "https://example.com/MyApp.ipa",
        }

    def test_generate_ota_manifest_skipped_when_unset(self):
        target = init_target(self.temp_dir)
        os.makedirs(target.buildozer.bin_dir, exist_ok=True)
        target._generate_ota_manifest("MyApp", "1.0")
        assert not os.path.exists(
            os.path.join(target.buildozer.bin_dir, "MyApp-1.0-manifest.plist")
        )

    # -- build_package integration ---------------------------------------

    def _run_build_package(self, options):
        """Run build_package with xcodebuild/keychain/plist mocked and return
        the patched buildops.cmd and logger.error mocks."""
        target = init_target(self.temp_dir, options)
        target.ios_dir = "/ios/dir"
        # fmt: off
        with patch_target_ios("_unlock_keychain"), \
             patch_logger_error() as m_error, \
             mock.patch("buildozer.targets.ios.TargetIos.load_plist_from_file") as m_load, \
             mock.patch("buildozer.targets.ios.TargetIos.dump_plist_to_file"), \
             patch_buildops_cmd() as m_cmd:
            m_load.return_value = {}
            target.build_package()
        # fmt: on
        return m_cmd, m_error

    def test_build_package_unsigned_passes_only_codesigning_allowed_no(self):
        """allowed=false: clean build/archive get only CODE_SIGNING_ALLOWED=NO;
        no signing tokens leak into xcodebuild and export is skipped."""
        m_cmd, m_error = self._run_build_package({})  # defaults -> allowed=false
        signing_prefixes = ("CODE_SIGN_STYLE", "CODE_SIGN_IDENTITY",
                            "DEVELOPMENT_TEAM", "PROVISIONING_PROFILE_SPECIFIER")
        # toolchain create, clean build, archive -- no export call
        assert len(m_cmd.call_args_list) == 3
        for index in (1, 2):
            args = m_cmd.call_args_list[index].args[0]
            assert "CODE_SIGNING_ALLOWED=NO" in args
            assert not any(a.startswith(signing_prefixes) for a in args)
        assert m_error.call_count == 1  # only "code signing disabled"

    def test_build_package_unsigned_manual_does_not_fast_fail(self):
        """allowed=false + manual + missing tokens must NOT fast-fail: the app
        is still built and only the export is skipped."""
        m_cmd, m_error = self._run_build_package(
            {"ios.codesign.style.debug": "manual"}
        )
        assert len(m_cmd.call_args_list) == 3  # build/archive ran
        assert m_error.call_count == 1  # only "code signing disabled"

    def test_build_package_signed_automatic_passes_style_and_team(self):
        """allowed=true + automatic + team: clean build/archive carry the style
        and the development team."""
        m_cmd, m_error = self._run_build_package(
            {
                "ios.codesign.allowed": "true",
                "ios.codesign.development_team.debug": "TEAM123",
            }
        )
        for index in (1, 2):
            args = m_cmd.call_args_list[index].args[0]
            assert "CODE_SIGNING_ALLOWED=YES" in args
            assert "CODE_SIGN_STYLE=Automatic" in args
            assert "DEVELOPMENT_TEAM=TEAM123" in args
        # The build proceeds into the export step; in this mocked environment no
        # real .ipa exists on disk, so export logs a single "no .ipa produced"
        # error. That is expected here and unrelated to the assertions above.
        assert m_error.call_count == 1

    def test_build_package_automatic_missing_team_fast_fails(self):
        """allowed=true + automatic + no team: stop before any xcodebuild."""
        m_cmd, m_error = self._run_build_package({"ios.codesign.allowed": "true"})
        assert m_cmd.call_args_list == []
        assert m_error.call_count == 1

    def test_build_package_signed_manual_passes_identity_and_profile(self):
        """allowed=true + manual complete: clean build/archive carry the set."""
        m_cmd, m_error = self._run_build_package(
            {
                "ios.codesign.allowed": "true",
                "ios.codesign.style.debug": "manual",
                "ios.codesign.debug": '"iPhone Distribution: ACME (TEAM123)"',
                "ios.codesign.provisioning_profile.debug": "MyProfile",
            }
        )
        for index in (1, 2):
            args = m_cmd.call_args_list[index].args[0]
            assert "CODE_SIGN_STYLE=Manual" in args
            assert "CODE_SIGN_IDENTITY=iPhone Distribution: ACME (TEAM123)" in args
            assert "PROVISIONING_PROFILE_SPECIFIER=MyProfile" in args
        # The build proceeds into the export step; in this mocked environment no
        # real .ipa exists on disk, so export logs a single "no .ipa produced"
        # error. That is expected here and unrelated to the assertions above.
        assert m_error.call_count == 1

    def test_build_package_signed_manual_missing_fast_fails(self):
        """allowed=true + manual + missing tokens: stop before any xcodebuild."""
        m_cmd, m_error = self._run_build_package(
            {"ios.codesign.allowed": "true", "ios.codesign.style.debug": "manual"}
        )
        assert m_cmd.call_args_list == []
        assert m_error.call_count == 2
