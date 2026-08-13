# JMdict pack load measurement methodology

The historical implementation evidence for #104 Slice A used
[`scripts/measure_jmdict_load.py`](../scripts/measure_jmdict_load.py) on the Linux GitHub Actions
runner after each then-reviewed normalized pack had been downloaded and verified by the same CLI
path used by operators. That experiment compared the English and German packs before the active
product contract was simplified to an English-only local dictionary; German is no longer an active
bootstrap/runtime requirement.

Each measured pack was loaded in a fresh Python process. The script records:

- normalized file size in bytes;
- runtime dictionary entry count;
- wall-clock time spent constructing `JsonJmdictDictionary`;
- Linux `resource.getrusage(RUSAGE_SELF).ru_maxrss` in KiB.

The method intentionally does not subtract interpreter/import baseline RSS, and the observed
maximum RSS includes Python/runtime overhead for that process. The recorded English/German
comparison remains historical engineering evidence rather than a current support matrix, hard
memory limit, or service-level guarantee.
