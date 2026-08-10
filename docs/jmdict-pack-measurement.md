# JMdict pack load measurement methodology

The implementation evidence for #104 Slice A uses
[`scripts/measure_jmdict_load.py`](../scripts/measure_jmdict_load.py) on the Linux GitHub Actions
runner after each reviewed normalized pack has been downloaded and verified by the same CLI path
used by operators.

Each pack is measured in a fresh Python process. The script records:

- normalized file size in bytes;
- runtime dictionary entry count;
- wall-clock time spent constructing `JsonJmdictDictionary`;
- Linux `resource.getrusage(RUSAGE_SELF).ru_maxrss` in KiB.

The method intentionally does not subtract interpreter/import baseline RSS, and the observed
maximum RSS includes Python/runtime overhead for that process. English and German are therefore
compared using the same method, but the values are evidence rather than hard memory limits or
service-level guarantees.
