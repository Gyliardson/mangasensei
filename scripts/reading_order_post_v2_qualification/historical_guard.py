from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Historical H01-H16 are OBSERVED calibration/regression fixtures. These frozen
# digests cover exactly their image/input/annotation payloads. A future corpus
# may not evade the no-reuse boundary by renaming one of those payloads.
HISTORICAL_V2_HELDOUT_CONTENT_SHA256 = frozenset(
    {
        # annotations/H01-H16.json
        "a60dfe99dced1f709a2de7728535c5a210823c4da2b5b7a7f2d5d9919f5108a9",
        "5f83ccfbf543adc49dde89a66f94bbeb1fe46222c708be193a01b51049ac9aca",
        "2be29ee67472b83c586510a294bec84540441605fe4e112f3b3164f54d6f0fdb",
        "0b711df3ace6b4ca71ed9e5da53fded5c3bd6a8b4ff5e4bfff5616a39b7a6b61",
        "e69cb4db82433ed26212d40eea65e0c150969b510efc8b5022b3087402ffa54d",
        "0df120eea307e8fa82d92a466684d4a16fcfcd620c18e7960690e376e82579f3",
        "5c99d59ce40779f1c118572244a8bc7ee86d4bcd2538ef4140fb4382f342dcba",
        "fa7fd98949a8d52fd48cce0b3c3b83a44fcfe333a8159b6bf89af585f6bd9159",
        "0d03330061095521961e0af3e49147860987796b6ece93dadde7f37d5b441d10",
        "01a0b630d152682941facd4fb78361a22d2683e9c29511e5e98a700a92c08985",
        "3e3c159ed3790a561f9973de15f51124453d11afa5e349da614bac881b47d03e",
        "96315834b00c15fedfeee9a8c2ae327408287baeb87d2bf628623ec0c9801ca0",
        "10d5e9efc7102aa5ab4f0f60a294438638fc83cbe2945522b128c10835e97f40",
        "639c9b600939a0e81b3ec763a2a2cd48184e32be615eb3cfc61bfc1eef45a9e4",
        "37bab614d2972ce8ac895bbe1c8678633690227da52e9afada12eb498d48566a",
        "cf1e04a5e470b969abcc70b64960194c651cd3c1897538fbb08305357384f903",
        # images/H01-H16.png
        "635705ebc33a98ba3af3b5b7990656c11f42ed498130726d5fb80919d36bcf85",
        "f67e5b7797d6f325a3b3ccd8ceca6e86da88b81eb7d392dcbc031d744183476f",
        "bbba04c7c32780997dc237674c5cd7d2c04a4bbe1142064276ffa65d4f17da40",
        "5ffb2167f48cc48a170bb220b7267818f073a110b70fe0cdb3d5985b14093248",
        "682dc2669e3e74502d65a19d405a2aa5934cc65cc77bfebb54aab03d7c1639fc",
        "2f530b0a4f1cfdfcbca8173ab320e302c7e0ecc789c51097ce0b5f8a4bba176f",
        "f72c6d00840aa4bf0f968d64caf5218383ecaa87839f23d82cf0fab347b9267c",
        "d46ba6d8fb1210691e343358248b70dbbd90b55de86bc171a85038c1acf8c78d",
        "14c375ad39095521961e0af3e49147860987796b6ece93dadde7f37d5b441d10",
        "e2fa48573c3e191425376e3a83787ce128d45f8971e1f055a2e73188dc965fd4",
        "6039417ec091b40d7b2b03fabf0f44910d6f5db15efe8f194e2809eecb4db837",
        "6e518ecc3e8bb5dfcc61926ca26a0c6b5d9f274cd52f4a3a6601613af709bab8",
        "3871b02083296b08faae78c600f66a8421ee4551d4e511b0d38587e9893f6709",
        "a30a31fbbdd5a3b274708b0292593292526fc8b69ed80df0387e77cc264c9ef0",
        "60752976ce0871b93152269918a27a81ff700798d2ae067193042ab9cf916ea6",
        "35f99c60c30f86b8e1f2c53fe444093afad2d4f96299b15c73d9e3a04566200a",
        # inputs/H01-H16.json
        "f6ab3088ab21d32410f445e6e4f9ed226a5a38ae93743014a32fcfd485ea729f",
        "af4a69d76e8731b1bba1d5c5412da9bb49855f3d0f67e6c06342c2655368fe58",
        "64ddb6114b56ac05d4413184534a61983658920e5055a3c993a8760293ef9c50",
        "71ac0e0e9187dace1cc30131f9d2a21d98ee51e9efc8436cb2329d9d53a64cf5",
        "fa910c5acba7697d6ea69163e8b5a740485e0a9bd6298ba15982ecb5794c301f",
        "9db3c22288683e952995bc6122667cc68a1a9ed1b78a704fccb6ab8a05670a7c",
        "4570874a4b69a9542a1e819abea6aeb6fe9201c21db9d5cc26b18113faa855c6",
        "4aeebb1f7926fd6abaa2c3d31a310eda76e0271f6236283432863f4620dae495",
        "2925e67af099eacfccbb24be825a0b634bb25255b94d518a6bd1460eba009ff0",
        "d80f610ccdafa8884c705bae6a89a944ba0bca9f14bdf94110ec63e4a51efb6d",
        "7f6ed26a92976f300fb9474acc72a14533d6574683e05d90f3eedb164a6e4048",
        "d5ebefdfeeb59eecb178c9a0ad88587804efa83c8f86f1a62f04e4b70905013a",
        "f9af4c650c44bdf3d1f7836039ac8eba01b22855314c26e847063cf5be8f3a1c",
        "6206dafa31b336957bb79cd6150186137c6e411928a85ad62b5bf3832c305de5",
        "ba00182210b620694d47a2c67e93496865e32299b9fa563c09641c54c661b78f",
        "0f32eb1797970e022da89b8d03545715a93d550fc6ba879e14ed78145dd5ce2d",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RETIRED_POST_V2_V1_MANIFEST = (
    REPO_ROOT / "assets" / "reading-order-post-v2" / "heldout-v1" / "manifest.json"
)


def _manifest_inventory(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("inventory"), list):
        raise ValueError("future corpus manifest must contain an inventory array")
    return [item for item in payload["inventory"] if isinstance(item, dict)]


def _retired_post_v2_v1_content_sha256() -> frozenset[str]:
    if not RETIRED_POST_V2_V1_MANIFEST.is_file():
        raise FileNotFoundError("retired post-v2 held-out v1 manifest is missing")
    digests: set[str] = set()
    for item in _manifest_inventory(RETIRED_POST_V2_V1_MANIFEST):
        digest = item.get("sha256")
        if isinstance(digest, str):
            digests.add(digest)
    if not digests:
        raise ValueError("retired post-v2 held-out v1 manifest has no content hashes")
    return frozenset(digests)


def assert_no_historical_v2_content_reuse(corpus_root: Path) -> None:
    manifest_path = corpus_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("future sealed corpus manifest is missing")
    retired_post_v2 = _retired_post_v2_v1_content_sha256()
    reused_historical: list[str] = []
    reused_retired_post_v2: list[str] = []
    for item in _manifest_inventory(manifest_path):
        relative = item.get("file")
        digest = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            continue
        if digest in HISTORICAL_V2_HELDOUT_CONTENT_SHA256:
            reused_historical.append(relative)
        if digest in retired_post_v2:
            reused_retired_post_v2.append(relative)
    if reused_historical:
        raise ValueError(
            "historical H01-H16 held-out content hash reuse is forbidden: "
            + ", ".join(sorted(reused_historical))
        )
    if reused_retired_post_v2:
        raise ValueError(
            "retired post-v2 held-out v1 content hash reuse is forbidden: "
            + ", ".join(sorted(reused_retired_post_v2))
        )
