# Changelog

All notable changes are documented here. This project distinguishes the software package version, benchmark specification/methodology version, result schema version, profile schema version, and external AIPerf engine version. Current values are software `0.0.1`, specification `0.5.0`, result schema `0.1.0`, profile schema `1.0`, and AIPerf target `0.13.0`.

## [Unreleased]

### Changed

- Standardized `endpoint-bench` as the canonical CLI command and `--profile` as the profile-selection option.
- Clarified that unknown pricing is allowed for offline validation/dry-run with an explicit null cost state, but blocks unattended live execution.
- Replaced YAML-specific requirements with format-neutral declarative configuration contracts.
- Separated pre-alpha source, benchmark MVP, and expanded operational qualification milestones.

### Security

- Expanded ignored credential, key, result, trace, AA snapshot, log, scratch, and machine-configuration patterns.

### Licensing

- Adopted the MIT License after owner approval.

## [0.0.1] — Unreleased pre-alpha

### Added

- Python 3.13 package and `endpoint-bench` CLI entry point.
- Offline provider registry validation and network-free dry-run manifest generation.
- Initial methodology and result-schema contracts.
- Contribution, security, development, licensing-decision, and changelog documentation.
- Offline tests, synthetic configuration/example, and GitHub Actions CI.
