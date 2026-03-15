# nf-core Standards Changelog

## 3.5.0 (2025-01-15)

- Use topic channels for versions output (versions_use_topic_channels: true)
- Standardised process labels: process_single, process_medium, process_high, process_high_memory
- Container: both Docker (quay.io/biocontainers) and Singularity (depot.galaxyproject.org) required
- meta.yml: EDAM ontology terms required where known
- All params via ext.args in conf/modules.config, never hardcoded in module
