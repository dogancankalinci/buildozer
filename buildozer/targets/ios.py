'''
iOS target, based on kivy-ios project
'''


from getpass import getpass
from glob import glob
from os.path import join, basename, expanduser, realpath
import plistlib
import sys

import buildozer.buildops as buildops
from buildozer.exceptions import BuildozerCommandException
from buildozer.target import Target, no_config


class TargetIos(Target):
    targetname = "ios"

    def __init__(self, buildozer):
        super().__init__(buildozer)
        executable = sys.executable or 'python'
        self._toolchain_cmd = [executable, "toolchain.py"]
        self._xcodebuild_cmd = ["xcodebuild"]
        # set via install_platform()
        self.ios_dir = None
        self.ios_deploy_dir = None

    def check_requirements(self):
        if sys.platform != "darwin":
            raise NotImplementedError("Only macOS is supported for iOS target")

        buildops.checkbin('Xcode xcodebuild', 'xcodebuild')
        buildops.checkbin('Xcode xcode-select', 'xcode-select')
        buildops.checkbin('Git git', 'git')
        buildops.checkbin('Cython cython', 'cython')
        buildops.checkbin('pkg-config', 'pkg-config')
        buildops.checkbin('autoconf', 'autoconf')
        buildops.checkbin('automake', 'automake')
        buildops.checkbin('libtool', 'libtool')

        self.logger.debug('Check availability of a iPhone SDK')
        sdk_list = buildops.cmd(
            ['xcodebuild', '-showsdks'],
            get_stdout=True,
            env=self.buildozer.environ).stdout
        iphoneos_lines = [
            line
            for line in sdk_list.split("\n")
            if "iphoneos" in line]
        if not iphoneos_lines:
            sdk = None
        else:
            last_iphoneos_line = iphoneos_lines[-1]
            sdk = last_iphoneos_line.split()[1]  # Second column

        if not sdk:
            raise Exception(
                'No iPhone SDK found. Please install at least one iOS SDK.')
        else:
            self.logger.debug(' -> found %r' % sdk)

        self.logger.debug('Check Xcode path')
        xcode = buildops.cmd(
            ["xcode-select", "-print-path"],
            get_stdout=True,
            env=self.buildozer.environ).stdout
        if not xcode:
            raise Exception('Unable to get xcode path')
        self.logger.debug(' -> found {0}'.format(xcode))

    def install_platform(self):
        """
        Clones `kivy/kivy-ios` and `phonegap/ios-deploy` then sets `ios_dir`
        and `ios_deploy_dir` accordingly.
        """
        self.ios_dir = self.install_or_update_repo('kivy-ios', platform='ios')
        self.ios_deploy_dir = self.install_or_update_repo('ios-deploy',
                                                          platform='ios',
                                                          branch='1.7.0',
                                                          owner='phonegap')

    def toolchain(self, cmd, **kwargs):
        kwargs.setdefault('cwd', self.ios_dir)
        return buildops.cmd(
            [*self._toolchain_cmd, *cmd],
            env=self.buildozer.environ,
            **kwargs)

    def xcodebuild(self, *args, **kwargs):
        filtered_args = [arg for arg in args if arg is not None]
        return buildops.cmd(
            [*self._xcodebuild_cmd, *filtered_args],
            env=self.buildozer.environ,
            **kwargs)

    @property
    def code_signing_enabled(self):
        return self.buildozer.config.getboolean("app", "ios.codesign.allowed")

    @property
    def code_signing_allowed(self):
        return "CODE_SIGNING_ALLOWED={}".format(
            "YES" if self.code_signing_enabled else "NO")

    @property
    def code_signing_style(self):
        """Configured signing style for the current build mode, either
        "automatic" or "manual".

        "automatic" lets Xcode resolve certificates/profiles through an active
        Apple ID session and therefore cannot be used on headless CI; "manual"
        relies on explicitly provided certificate and provisioning profile.
        """
        key = "ios.codesign.style.{}".format(self.build_mode)
        style = self.buildozer.config.get(
            "app", key, fallback="automatic").lower()
        if style not in ("automatic", "manual"):
            self.logger.error(
                'Invalid {} "{}", falling back to "automatic". '
                'Valid values are "automatic" or "manual".'.format(key, style))
            style = "automatic"
        return style

    def _get_code_sign_identity(self):
        """Return the signing certificate name for the current build mode.

        The value is stripped of any surrounding quotes because these arguments
        are passed straight to a subprocess (there is no shell to unquote them).
        """
        key = "ios.codesign.{}".format(self.build_mode)
        identity = self.buildozer.config.get("app", key, fallback="")
        if (len(identity) >= 2 and identity[0] in ('"', "'")
                and identity[-1] == identity[0]):
            identity = identity[1:-1]
        return identity

    def _get_provisioning_profile(self):
        """Return the provisioning profile (name or UUID) configured for the
        current build mode. Only relevant for manual signing."""
        key = "ios.codesign.provisioning_profile.{}".format(self.build_mode)
        return self.buildozer.config.get("app", key, fallback="")

    def _get_signing_build_settings(self):
        """Return the xcodebuild build settings for the ``clean build`` and
        ``archive`` steps.

        When code signing is disabled (``ios.codesign.allowed = false``) no
        signing settings are returned at all: the build is unsigned and only
        ``CODE_SIGNING_ALLOWED=NO`` (passed separately) is given to xcodebuild.

        When signing is enabled, ``CODE_SIGN_STYLE`` is always passed explicitly
        so the build does not silently depend on whatever value happens to be
        saved in the generated ``.xcodeproj``. Manual signing (the only style
        usable on headless CI, where there is no Apple ID session) additionally
        pins the certificate and the provisioning profile.
        """
        if not self.code_signing_enabled:
            return []
        settings = []
        team = self.buildozer.config.get(
            "app", "ios.codesign.development_team.{}".format(self.build_mode),
            fallback=None)
        if team:
            settings.append("DEVELOPMENT_TEAM={}".format(team))
        if self.code_signing_style == "manual":
            settings.append("CODE_SIGN_STYLE=Manual")
            identity = self._get_code_sign_identity()
            if identity:
                settings.append("CODE_SIGN_IDENTITY={}".format(identity))
            profile = self._get_provisioning_profile()
            if profile:
                settings.append(
                    "PROVISIONING_PROFILE_SPECIFIER={}".format(profile))
        else:
            settings.append("CODE_SIGN_STYLE=Automatic")
        return settings

    def _validate_export_signing(self):
        """Check the configuration is sufficient to sign and export an IPA.

        Signing is only validated when it is enabled; with signing disabled
        there is nothing to sign, so the configuration is ignored until the
        export step. Automatic signing lets Xcode resolve the certificate and
        provisioning profile via the Apple ID session, but still needs a
        development team. Manual signing has no such fallback and needs a
        signing identity and a provisioning profile (the team is carried by the
        profile). An error is logged for each missing token.
        """
        if not self.code_signing_enabled:
            return True
        if self.code_signing_style != "manual":
            # Automatic signing still needs to know which Developer Team to use;
            # without it xcodebuild fails with "requires a development team".
            team_key = "ios.codesign.development_team.{}".format(self.build_mode)
            if not self.buildozer.config.get("app", team_key, fallback=""):
                self.logger.error(
                    'Automatic code signing requires a development team. '
                    'You must fill the "{}" token.'.format(team_key))
                return False
            return True
        ok = True
        identity_key = "ios.codesign.{}".format(self.build_mode)
        if not self.buildozer.config.get("app", identity_key, fallback=""):
            self.logger.error(
                'Manual code signing requires a signing certificate. '
                'You must fill the "{}" token.'.format(identity_key))
            ok = False
        if not self._get_provisioning_profile():
            profile_key = "ios.codesign.provisioning_profile.{}".format(
                self.build_mode)
            self.logger.error(
                'Manual code signing requires a provisioning profile. '
                'You must fill the "{}" token.'.format(profile_key))
            ok = False
        return ok

    def get_available_packages(self):
        available_modules = self.toolchain(["recipes", "--compact"], get_stdout=True)[0]
        return available_modules.splitlines()[0].split()

    def load_plist_from_file(self, plist_rfn):
        with open(plist_rfn, 'rb') as f:
            return plistlib.load(f)

    def dump_plist_to_file(self, plist, plist_rfn):
        with open(plist_rfn, 'wb') as f:
            plistlib.dump(plist, f)

    def compile_platform(self):
        # for ios, the compilation depends really on the app requirements.
        # compile the distribution only if the requirements changed.
        last_requirements = self.buildozer.state.get('ios.requirements', '')
        app_requirements = self.buildozer.config.getlist('app', 'requirements',
                '')

        # we need to extract the requirements that kivy-ios knows about
        available_modules = self.get_available_packages()
        onlyname = lambda x: x.split('==')[0]  # noqa: E731 do not assign a lambda expression, use a def
        ios_requirements = [x for x in app_requirements if onlyname(x) in
                            available_modules]

        need_compile = 0
        if last_requirements != ios_requirements:
            need_compile = 1

        # len('requirements.source.') == 20, so use name[20:]
        source_dirs = {'{}_DIR'.format(name[20:].upper()):
                            realpath(expanduser(value))
                       for name, value in self.buildozer.config.items('app')
                       if name.startswith('requirements.source.')}
        if source_dirs:
            need_compile = 1
            self.buildozer.environ.update(source_dirs)
            self.logger.info('Using custom source dirs:\n    {}'.format(
                '\n    '.join(['{} = {}'.format(k, v)
                               for k, v in source_dirs.items()])))

        if not need_compile:
            self.logger.info('Distribution already compiled, pass.')
            return

        self.toolchain(["build", *ios_requirements])

        if not buildops.file_exists(
                join(self.ios_deploy_dir, 'ios-deploy')):
            self.xcodebuild(cwd=self.ios_deploy_dir)

        self.buildozer.state['ios.requirements'] = ios_requirements
        self.buildozer.state.sync()

    def _get_package(self):
        config = self.buildozer.config
        package_domain = config.get('app', 'package.domain', fallback='')
        package = config.get('app', 'package.name')
        if package_domain:
            package = package_domain + '.' + package
        return package.lower()

    def build_package(self):
        # Fail fast on an incomplete manual-signing configuration: the
        # `clean build` and `archive` steps below already consume the signing
        # identity/profile, so validating up front gives a clear message instead
        # of a cryptic xcodebuild failure.
        if not self._validate_export_signing():
            return

        self._unlock_keychain()

        # create the project
        app_name = self.buildozer.namify(self.buildozer.config.get('app',
            'package.name'))

        ios_frameworks = self.buildozer.config.getlist('app', 'ios.frameworks', '')
        frameworks_cmd = []
        for framework in ios_frameworks:
            frameworks_cmd.append(f"--add-framework={framework}")

        self.app_project_dir = join(self.ios_dir, '{0}-ios'.format(app_name.lower()))
        if not buildops.file_exists(self.app_project_dir):
            cmd = ["create", *frameworks_cmd, app_name, self.buildozer.app_dir]
        else:
            cmd = ["update", *frameworks_cmd, f"{app_name}-ios"]
        self.toolchain(cmd)

        # fix the plist
        plist_fn = '{}-Info.plist'.format(app_name.lower())
        plist_rfn = join(self.app_project_dir, plist_fn)
        version = self.buildozer.get_version()
        title = self.buildozer.config.get('app', 'title')
        self.logger.info('Update Plist {}'.format(plist_fn))
        plist = self.load_plist_from_file(plist_rfn)
        plist['CFBundleDisplayName'] = title
        plist['CFBundleIdentifier'] = self._get_package()
        plist['CFBundleName'] = title
        plist['CFBundleShortVersionString'] = version
        plist['CFBundleVersion'] = '{}.{}'.format(version,
                self.buildozer.build_id)

        # add icons
        self._create_icons()

        # Permissions and their Justification Descriptions
        local_network_usage_description = self.buildozer.config.get(
            "app", "ios.local_network_usage_description", fallback=None)
        media_usage_description = self.buildozer.config.get(
            "app", "ios.media_usage_description", fallback=None)
        camera_usage_description = self.buildozer.config.get(
            "app", "ios.camera_usage_description", fallback=None)
        viewcontroller_based_statusbar_appearance = self.buildozer.config.get(
            "app", "ios.viewcontroller_based_statusbar_appearance", fallback=None)

        # types
        custom_ext_types = self.buildozer.config.get("app", "ios.app_extensions", fallback=None)

        if media_usage_description:
            plist['NSAppleMusicUsageDescription'] = media_usage_description
        if local_network_usage_description:
            plist['NSLocalNetworkUsageDescription'] = local_network_usage_description
        if camera_usage_description:
            plist['NSCameraUsageDescription'] = camera_usage_description
        if viewcontroller_based_statusbar_appearance:
            plist['UIViewControllerBasedStatusBarAppearance'] = viewcontroller_based_statusbar_appearance
        if custom_ext_types:
            import ast
            custom_ext_types = ast.literal_eval(custom_ext_types)
            plist["UTExportedTypeDeclarations"] = []
            plist["CFBundleDocumentTypes"] = []
            for ext in custom_ext_types:
                plist["UTExportedTypeDeclarations"].append({
                    'UTTypeConformsTo': ext[1],
                    'UTTypeIdentifier': ext[2],
                    'UTTypeDescription': ext[3],
                    'UTTypeIconFile': ext[4],
                    'UTTypeReferenceURL': ext[5],
                    'UTTypeTagSpecification': {'public.filename-extension': ext[0]}, })
                plist["CFBundleDocumentTypes"].append({
                    "CFBundleTypeName": ext[3],
                    "CFBundleTypeIconFile": ext[4],
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Owner",
                    "LSItemContentTypes": [ext[2]], })
            plist["LSSupportsOpeningDocumentsInPlace"] = "NO"
            plist["UISupportsDocumentBrowser"] = "NO"

        # ok, write the modified plist.
        self.dump_plist_to_file(plist, plist_rfn)

        mode = self.build_mode.capitalize()
        # Signing settings shared by the `clean build` and `archive` steps.
        # When signing is disabled this list is empty, so only
        # CODE_SIGNING_ALLOWED=NO is passed; otherwise CODE_SIGN_STYLE (and, for
        # manual signing, the team/identity/profile) are pinned explicitly.
        signing_build_settings = self._get_signing_build_settings()
        self.xcodebuild(
            "-configuration",
            mode,
            '-allowProvisioningUpdates',
            'ENABLE_BITCODE=NO',
            self.code_signing_allowed,
            *signing_build_settings,
            'clean',
            'build',
            cwd=self.app_project_dir)
        ios_app_dir = '{app_lower}-ios/build/{mode}-iphoneos/{app_lower}.app'.format(
                app_lower=app_name.lower(), mode=mode)
        self.buildozer.state['ios:latestappdir'] = ios_app_dir

        intermediate_dir = join(self.ios_dir, '{}-{}.intermediates'.format(app_name, version))
        xcarchive = join(intermediate_dir, '{}-{}.xcarchive'.format(
            app_name, version))
        ipa_name = '{}-{}.ipa'.format(app_name, version)
        ipa = join(self.buildozer.bin_dir, ipa_name)
        build_dir = join(self.ios_dir, '{}-ios'.format(app_name.lower()))

        buildops.rmdir(intermediate_dir)

        self.logger.info('Creating archive...')
        self.xcodebuild(
            '-alltargets',
            '-configuration',
            mode,
            '-scheme',
            app_name.lower(),
            '-archivePath',
            xcarchive,
            '-destination',
            'generic/platform=iOS',
            '-allowProvisioningUpdates',
            'archive',
            'ENABLE_BITCODE=NO',
            self.code_signing_allowed,
            *signing_build_settings,
            cwd=build_dir)

        # An IPA cannot be exported without code signing enabled. The app has
        # already been built at this point (so deploy/run still work), but stop
        # before the export step with a clear message rather than letting
        # xcodebuild fail trying to export an unsigned archive.
        if not self.code_signing_enabled:
            self.logger.error(
                'Code signing is disabled (ios.codesign.allowed = false); '
                'the app was built but no IPA can be produced. Set '
                'ios.codesign.allowed = true to create a signed IPA.')
            return

        # Export needs a dedicated ExportOptions.plist (NOT the app Info.plist),
        # and every flag/value must be a separate argument because the command
        # is run directly via subprocess (no shell to split arguments on spaces).
        export_options_plist = self._generate_export_options_plist(
            intermediate_dir)
        export_dir = join(intermediate_dir, 'export')

        self.logger.info('Creating IPA...')
        self.xcodebuild(
            '-exportArchive',
            '-archivePath', xcarchive,
            '-exportOptionsPlist', export_options_plist,
            '-exportPath', export_dir,
            'ENABLE_BITCODE=NO',
            cwd=build_dir)

        # xcodebuild writes the IPA (named after the scheme) into the export
        # directory; locate it before moving it to the bin directory.
        exported_ipas = glob(join(export_dir, '*.ipa'))
        if not exported_ipas:
            self.logger.error(
                'Export finished but no .ipa was produced in {}'.format(
                    export_dir))
            return

        self.logger.info('Moving IPA to bin...')
        buildops.rename(exported_ipas[0], ipa)

        # Generate a standalone OTA (over-the-air) distribution manifest when
        # the ios.manifest.* options are set. iOS reads OTA data from this
        # dedicated plist hosted next to the IPA, never from the app Info.plist.
        self._generate_ota_manifest(app_name, version)

        self.logger.info('iOS packaging done!')
        self.logger.info('IPA {0} available in the bin directory'.format(
            basename(ipa)))
        self.buildozer.state['ios:latestipa'] = ipa
        self.buildozer.state['ios:latestmode'] = self.build_mode

    def _generate_export_options_plist(self, intermediate_dir):
        """Create the ExportOptions.plist consumed by ``xcodebuild
        -exportArchive``.

        This is a distribution-options file (export method, team id, signing
        style, ...) and must not be confused with the application Info.plist.
        """
        config = self.buildozer.config
        style = self.code_signing_style
        export_options = {
            'method': config.get(
                'app', 'ios.export_method.{}'.format(self.build_mode),
                fallback='development'),
            'signingStyle': style,
        }
        team = config.get(
            'app', 'ios.codesign.development_team.{}'.format(self.build_mode),
            fallback=None)
        if team:
            export_options['teamID'] = team
        if style == 'manual':
            identity = self._get_code_sign_identity()
            if identity:
                export_options['signingCertificate'] = identity
            profile = self._get_provisioning_profile()
            if profile:
                export_options['provisioningProfiles'] = {
                    self._get_package(): profile,
                }
        export_options_plist = join(intermediate_dir, 'ExportOptions.plist')
        self.dump_plist_to_file(export_options, export_options_plist)
        return export_options_plist

    def _generate_ota_manifest(self, app_name, version):
        """Write a standalone iTunes Services manifest plist into the bin
        directory for OTA (over-the-air) distribution.

        This is the file an ``itms-services://?action=download-manifest&url=...``
        link must point at. It is only generated when the three ios.manifest.*
        options are configured together.
        """
        config = self.buildozer.config
        app_url = config.get('app', 'ios.manifest.app_url', fallback=None)
        display_image_url = config.get(
            'app', 'ios.manifest.display_image_url', fallback=None)
        full_size_image_url = config.get(
            'app', 'ios.manifest.full_size_image_url', fallback=None)

        if not any((app_url, display_image_url, full_size_image_url)):
            return

        if not all((app_url, display_image_url, full_size_image_url)):
            self.logger.error(
                'Options ios.manifest.app_url, ios.manifest.display_image_url '
                'and ios.manifest.full_size_image_url should be defined all '
                'together; skipping OTA manifest generation.')
            return

        manifest = {
            'items': [{
                'assets': [
                    {'kind': 'software-package', 'url': app_url},
                    {'kind': 'display-image', 'needs-shine': False,
                     'url': display_image_url},
                    {'kind': 'full-size-image', 'needs-shine': False,
                     'url': full_size_image_url},
                ],
                'metadata': {
                    'bundle-identifier': self._get_package(),
                    'bundle-version': version,
                    'kind': 'software',
                    'title': app_name,
                },
            }],
        }
        manifest_path = join(
            self.buildozer.bin_dir,
            '{}-{}-manifest.plist'.format(app_name, version))
        self.dump_plist_to_file(manifest, manifest_path)
        self.logger.info(
            'OTA manifest available in the bin directory: {}'.format(
                basename(manifest_path)))

    def cmd_deploy(self, *args):
        super().cmd_deploy(*args)
        self._run_ios_deploy(lldb=False)

    def cmd_run(self, *args):
        super().cmd_run(*args)
        self._run_ios_deploy(lldb=True)

    def cmd_xcode(self, *args):
        '''Open the xcode project.
        '''
        app_name = self.buildozer.namify(self.buildozer.config.get('app',
            'package.name'))
        app_name = app_name.lower()

        ios_dir = join(self.buildozer.platform_dir, 'kivy-ios')
        buildops.cmd(
            ["open", f"{app_name}.xcodeproj"],
            cwd=join(ios_dir, f"{app_name}-ios"),
            env=self.buildozer.environ
        )

    def _run_ios_deploy(self, lldb=False):
        state = self.buildozer.state
        if 'ios:latestappdir' not in state:
            self.logger.error(
                'App not built yet. Run "debug" or "release" first.')
            return
        ios_app_dir = state.get('ios:latestappdir')

        if lldb:
            debug_mode = '-d'
            self.logger.info('Deploy and start the application')
        else:
            debug_mode = ''
            self.logger.info('Deploy the application')

        buildops.cmd(
            [join(self.ios_deploy_dir, "ios-deploy"), debug_mode, "-b", ios_app_dir],
            cwd=self.ios_dir,
            show_output=True,
            env=self.buildozer.environ)

    def _create_icons(self):
        icon = self.buildozer.config.get('app', 'icon.filename', fallback='')
        if not icon:
            return
        icon_fn = join(self.buildozer.app_dir, icon)
        if not buildops.file_exists(icon_fn):
            self.logger.error('Icon {} does not exists'.format(icon_fn))
            return

        self.toolchain(["icon", self.app_project_dir, icon_fn])

    def check_configuration_tokens(self):
        errors = []
        config = self.buildozer.config
        if not config.getboolean('app', 'ios.codesign.allowed'):
            return
        identity_debug = config.get('app', 'ios.codesign.debug', fallback='')
        identity_release = config.get('app', 'ios.codesign.release',
                fallback=identity_debug)
        available_identities = self._get_available_identities()

        if not identity_debug:
            errors.append('[app] "ios.codesign.debug" key missing, '
                    'you must give a certificate name to use.')
        elif identity_debug not in available_identities:
            errors.append('[app] identity {} not found. '
                    'Check with list_identities'.format(identity_debug))

        if not identity_release:
            errors.append('[app] "ios.codesign.release" key missing, '
                    'you must give a certificate name to use.')
        elif identity_release not in available_identities:
            errors.append('[app] identity "{}" not found. '
                    'Check with list_identities'.format(identity_release))
        super().check_configuration_tokens(errors)

    @no_config
    def cmd_list_identities(self, *args):
        '''List the available identities to use for signing.
        '''
        identities = self._get_available_identities()
        print('Available identities:')
        for x in identities:
            print('  - {}'.format(x))

    def _get_available_identities(self):
        output = buildops.cmd(
            ["security", "find-identity", "-v", "-p", "codesigning"],
            get_stdout=True,
            env=self.buildozer.environ
        ).stdout

        lines = output.splitlines()[:-1]
        lines = [u'"{}"'.format(x.split('"')[1]) for x in lines]
        return lines

    def _unlock_keychain(self):
        password_file = join(self.buildozer.buildozer_dir, '.ioscodesign')
        password = None
        if buildops.file_exists(password_file):
            with open(password_file) as fd:
                password = fd.read()

        if not password:
            # no password available, try to unlock anyway...
            error = buildops.cmd(
                ["security", "unlock-keychain", "-u"],
                break_on_error=False,
                quiet=True,  # Log doesn't need secure info
                env=self.buildozer.environ).return_code
            if not error:
                return
        else:
            # password available, try to unlock
            error = buildops.cmd(
                ["security", "unlock-keychain", "-p", password],
                break_on_error=False,
                quiet=True,  # Log doesn't need secure info
                env=self.buildozer.environ
            ).return_code
            if not error:
                return

        # we need the password to unlock.
        correct = False
        attempt = 3
        while attempt:
            attempt -= 1
            password = getpass('Password to unlock the default keychain:')
            error = buildops.cmd(
                ["security", "unlock-keychain", "-p", password],
                quiet=True,  # Log doesn't need secure info
                break_on_error=False,
                env=self.buildozer.environ
            ).return_code
            if not error:
                correct = True
                break
            self.logger.error('Invalid keychain password')

        if not correct:
            self.logger.error('Unable to unlock the keychain, exiting.')
            raise BuildozerCommandException()

        # maybe user want to save it for further reuse?
        print(
            'The keychain password can be saved in the build directory\n'
            'As soon as the build directory will be cleaned, '
            'the password will be erased.')

        save = None
        while save is None:
            q = input('Do you want to save the password (Y/n): ')
            if q in ('', 'Y'):
                save = True
            elif q == 'n':
                save = False
            else:
                print('Invalid answer!')

        if save:
            with open(password_file, 'wb') as fd:
                fd.write(password.encode())


def get_target(buildozer):
    return TargetIos(buildozer)
