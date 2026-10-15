"""Extract the full icon group embedded in a PE (.exe/.dll) into an .ico.
Usage: py -3.10 extract_pe_icon.py <pe-file> <out.ico>
Stdlib only. Picks the lowest-id RT_GROUP_ICON and dumps every size in it.
"""
import struct, sys


def rva2off(secs, rva):
    for va, vsz, raw, rsz in secs:
        if va <= rva < va + max(vsz, rsz):
            return raw + (rva - va)
    raise ValueError(f"rva {rva:#x} not in any section")


def main(pe_path, out_path):
    with open(pe_path, "rb") as f:
        f.seek(0x3C)
        pe = struct.unpack("<I", f.read(4))[0]
        f.seek(pe)
        assert f.read(4) == b"PE\0\0", "not a PE file"
        nsec = struct.unpack("<H", f.read(2))[0]
        f.seek(pe + 20)
        optsz = struct.unpack("<H", f.read(2))[0]
        opt = pe + 24
        f.seek(opt)
        magic = struct.unpack("<H", f.read(2))[0]
        f.seek(opt + (112 if magic == 0x20B else 96) + 2 * 8)
        res_rva, _ = struct.unpack("<II", f.read(8))
        assert res_rva, "no resource directory"
        secs = []
        for i in range(nsec):
            f.seek(opt + optsz + i * 40 + 8)
            vsz, va, rsz, raw = struct.unpack("<IIII", f.read(16))
            secs.append((va, vsz, raw, rsz))
        res_base = rva2off(secs, res_rva)

        def read(off, n):
            f.seek(off)
            return f.read(n)

        def entries(dir_off):
            """[(id, is_dir, absolute_offset)] for one resource directory"""
            n_named, n_id = struct.unpack("<HH", read(dir_off + 12, 4))
            out = []
            for i in range(n_named + n_id):
                name, off = struct.unpack("<II", read(dir_off + 16 + i * 8, 8))
                out.append((name & 0x7FFFFFFF, bool(off & 0x80000000),
                            res_base + (off & 0x7FFFFFFF)))
            return out

        def leaf(off):
            rva, size = struct.unpack("<II", read(off, 8))
            return rva2off(secs, rva), size

        groups, icons = {}, {}          # RT_GROUP_ICON=14, RT_ICON=3
        for tid, isdir, toff in entries(res_base):
            if not isdir:
                continue
            for nid, isd2, o2 in entries(toff):
                if not isd2:
                    continue
                for lang, isd3, o3 in entries(o2):
                    if isd3:
                        continue
                    if tid == 14:
                        groups.setdefault(nid, []).append(leaf(o3))
                    elif tid == 3:
                        icons[(nid, lang)] = leaf(o3)
        assert groups, "no RT_GROUP_ICON in this PE"
        gid = min(groups)
        goff, gsize = groups[gid][0]
        blob = read(goff, gsize)
        _, _, count = struct.unpack("<HHH", blob[:6])
        imgs, ico_entries = [], b""
        for i in range(count):
            e = blob[6 + i * 14: 6 + (i + 1) * 14]
            w, h, cc, r, planes, bits = struct.unpack("<BBBBHH", e[:8])
            icon_id = struct.unpack("<H", e[12:14])[0]
            cand = [v for (iid, _), v in icons.items() if iid == icon_id]
            assert cand, f"RT_ICON {icon_id} missing"
            data = read(*cand[0])
            ico_entries += struct.pack("<BBBBHHII", w, h, cc, r, planes, bits,
                                       len(data),
                                       6 + 16 * count + sum(len(x) for x in imgs))
            imgs.append(data)
        ico = struct.pack("<HHH", 0, 1, count) + ico_entries + b"".join(imgs)
        with open(out_path, "wb") as g:
            g.write(ico)
        print(f"OK group={gid} sizes={count} bytes={len(ico)} -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
