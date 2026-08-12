# RPM for the TSRS status panel gateway.
#
# Ships the systemd unit, the site config, and the CA probe -- NOT the
# application, which stays in the container image on GHCR. The unit is pinned to
# the image tag matching this package's version, so `rpm -q tsrs-screen` tells
# you exactly what runs and `dnf downgrade` is a real rollback.
#
# THE VERSION LIVES HERE. Bump specver in a commit, then run the `release`
# workflow -- it reads this value, so the RPM version, the image tag and the git
# tag cannot disagree and nobody types a version twice.
%global specver 0.5.0

# Commit hash in the Release, as every other gemini-rtsw package does. It makes
# each build a distinct NVRA, so a dev build can never overwrite a released one
# in the shared repo, and `rpm -q` names the exact commit a host is running.
%define git_hash %(git rev-parse --short HEAD 2>/dev/null || echo nogit)

# _version override exists only for verify-rpm.sh, which builds a throwaway
# +1 package to test the upgrade path.
%global appimage ghcr.io/gemini-rtsw/tsrs_screen

Name:           tsrs-screen
Version:        %{?_version}%{!?_version:%{specver}}
Release:        1.git%{git_hash}%{?dist}
Summary:        TSRS status panel gateway (read-only EPICS CA bridge)

License:        Proprietary
URL:            https://github.com/gemini-rtsw/tsrs_screen
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  systemd-rpm-macros
Requires:       systemd
Requires:       python3

# Deliberately NOT `Requires: /usr/bin/docker`. Docker on these hosts is not
# always installed from an RPM that declares that file, and a dependency that
# cannot be satisfied turns a working deployment into a failed `dnf install`.
# The unit's Requires=docker.service catches a genuinely missing daemon at
# start time, where the error is legible.

%description
Read-only EPICS Channel Access bridge and web panel replacing the CS-Studio
TSRS screen: 68 channels from the BFO PLC rendered as Ready / Not Ready
indicators, plus Observatory Mode.

This package contains only the systemd unit, its site configuration and the
CA reachability probe. The gateway itself runs from the container image
%{appimage}:%{version}, which the unit pulls on start.

After installing, set the IOC address in /etc/sysconfig/tsrs-web, then:

    tsrs-ca-probe <ioc-ip> bfo:mcsStatus      # want: FOUND
    systemctl enable --now tsrs-web
    curl -s localhost:8090/api/healthz

%prep
%autosetup

%build
# Pin the unit to this package's version. Tag rather than digest, on purpose:
# a human reading the unit can tell at a glance what it runs, and it matches
# `rpm -q`. The v* git tag drives both, so they cannot disagree.
sed -e 's|@IMAGE@|%{appimage}:%{version}|' \
    -e 's|@VERSION@|%{version}-%{release}|' \
    deploy/tsrs-web.service.in > tsrs-web.service
grep -qE '@IMAGE@|@VERSION@' tsrs-web.service && { echo "ERROR: placeholder not substituted" >&2; exit 1; }
grep -q '^Environment=IMAGE=%{appimage}:%{version}$' tsrs-web.service \
    || { echo "ERROR: image pin missing or malformed" >&2; exit 1; }

%install
install -Dpm 0644 tsrs-web.service       %{buildroot}%{_unitdir}/tsrs-web.service
install -Dpm 0644 deploy/tsrs-web.sysconfig %{buildroot}%{_sysconfdir}/sysconfig/tsrs-web
install -Dpm 0755 tools/ca_probe.py      %{buildroot}%{_bindir}/tsrs-ca-probe
install -Dpm 0755 deploy/resolve-site.sh %{buildroot}%{_libexecdir}/tsrs-screen/resolve-site.sh
install -Dpm 0644 deploy/site-MK.env     %{buildroot}%{_datadir}/tsrs-screen/site-MK.env
install -Dpm 0644 deploy/site-CP.env     %{buildroot}%{_datadir}/tsrs-screen/site-CP.env

%post
%systemd_post tsrs-web.service

%preun
%systemd_preun tsrs-web.service

%postun
# Deliberately NOT %%systemd_postun_with_restart (%% because rpm expands macros
# even inside spec comments): this is a wall display, and
# restarting it out from under an operator mid-upgrade is worse than running
# the previous image until someone chooses to restart. The new image only takes
# effect on the next `systemctl restart tsrs-web`.
%systemd_postun tsrs-web.service

%files
%{_unitdir}/tsrs-web.service
%config(noreplace) %{_sysconfdir}/sysconfig/tsrs-web
%{_bindir}/tsrs-ca-probe
%{_libexecdir}/tsrs-screen/resolve-site.sh
# NOT %%config: these are the package's verified site facts, and an upgrade
# carrying a corrected IOC address must actually reach the host. Host-specific
# deviations belong in /etc/sysconfig/tsrs-web, which is %%config(noreplace).
%{_datadir}/tsrs-screen/site-MK.env
%{_datadir}/tsrs-screen/site-CP.env

%changelog
* Tue Aug 11 2026 Gemini RTSW <rtsw@gemini.edu> - 0.0.0-1
- Initial packaging. Version comes from the git tag at build time; this entry
  exists only because rpmbuild requires a non-empty changelog.
