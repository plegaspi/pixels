BLOSC_ZSTD = "blosc_zstd"
BLOSC_FILTER_ID = 32001


def require_hdf5plugin():
    try:
        import hdf5plugin
    except ImportError as exc:
        raise RuntimeError("blosc_zstd requires hdf5plugin") from exc
    return hdf5plugin


def blosc_zstd_kwargs(clevel: int):
    hdf5plugin = require_hdf5plugin()

    return hdf5plugin.Blosc(
        cname="zstd",
        clevel=clevel,
        shuffle=hdf5plugin.Blosc.BITSHUFFLE,
    )


def compression_kwargs(
    compression: str | None,
    compression_opts: int | None = None,
    *,
    blosc_clevel: int = 3,
):
    comp = (compression or "").strip().lower()
    if comp in ("", "none", "off", "false"):
        return {}
    if comp == "gzip":
        return {"compression": "gzip", "compression_opts": compression_opts or 1, "shuffle": True}
    if comp == "lzf":
        return {"compression": "lzf", "shuffle": True}
    if comp == BLOSC_ZSTD:
        return blosc_zstd_kwargs(blosc_clevel)
    raise ValueError("final_compression must be one of: gzip, lzf, blosc_zstd, none")


def require_hdf5plugin_for_dataset(dataset) -> None:
    dcpl = dataset.id.get_create_plist()
    for idx in range(dcpl.get_nfilters()):
        filter_info = dcpl.get_filter(idx)
        if int(filter_info[0]) == BLOSC_FILTER_ID:
            require_hdf5plugin()
            return
