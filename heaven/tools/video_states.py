#!/usr/bin/env python3
"""動画を「画面の状態」単位で切り出す。一定間隔の間引きではなく、画面が変わった瞬間を拾う。

一定間隔（fps=1 等）だと、1 秒未満しか出ない表示（ホバーのツールチップ、一瞬のダイアログ）を落とす。
かといって全フレームを細かく見るのは無駄が多い。そこで変化の「種類」で残し方を分ける:

  1. 低解像度・高フレームレート（既定 10fps）で全体を安く走査する
  2. 画面を格子に分け、「最後に残したフレーム」と比べて升ごとの変化を測る
  3. 一部の升だけが変わった（= ツールチップ・パネル・ダイアログが出た/消えた）→ そのフレームを必ず残す
  4. 画面の大半が変わった（= 戦闘・カメラ移動・場面転換）→ --motion-interval 秒に 1 枚だけ残す
  5. 変化が無い区間も --floor 秒ごとに 1 枚残す

3 は「次のフレームでも新しい表示が残っている」ものだけに絞る（炎・待機モーションなど動き続ける物を除く）。
メニュー・ホバー区間向け。戦闘が続く区間はエフェクトで枚数が膨らむので、全体を粗く見て情報のある区間を
特定してから --ss/--to でその区間にだけかける。枚数が多くても --ocr の結果を grep すれば、目で見る枚数は少なくて済む。

実測（ゲームの装備画面のホバー録画 138 秒）: 1fps 間引き 138 枚ではツールチップ 12 件中の大半を落とした。
このツール（既定値）は 238 枚で 12 件すべて（最短 0.2 秒のもの含む）を拾い、何もない区間は 30 秒あたり 7 枚程度。

出力: <out>/NNNN_<秒>.png（原寸）と <out>/index.tsv（時刻・理由・変化した升の割合）。

使い方:
  python3 video_states.py VIDEO --out DIR [--ss 100 --to 140] [--scan-fps 10] [--thresh 22]
                                 [--motion-frac 0.5] [--motion-interval 2] [--floor 5] [--ocr]
--ocr を付けると macOS Vision（同じフォルダの ocr_vision.swift）で文字認識し <out>/ocr.txt に書く。
「映っていない」と言う前に、この ocr.txt を部位名・項目名で grep する。
"""
import argparse, os, subprocess, json
import numpy as np

W, H = 192, 108          # 走査用の縮小サイズ
GX, GY = 12, 9           # 格子の升の数（1 升 = 16x12 px）


def cell_diff(a, b):
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return d.reshape(GY, H // GY, GX, W // GX).mean(axis=(1, 3))  # 升ごとの平均差


def scan(video, ss, to, fps):
    cmd = ["ffmpeg", "-v", "error"]
    if ss: cmd += ["-ss", str(ss)]
    cmd += ["-i", video]
    if to: cmd += ["-t", str(to - (ss or 0))]
    cmd += ["-vf", f"fps={fps},scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.check_output(cmd)
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W)


def pick(frames, fps, thresh, motion_frac, motion_interval, floor, persist=0.5):
    keep = [(0, "start", 0.0)]
    last, last_t, last_motion_t = frames[0], 0.0, -1e9
    for i in range(1, len(frames) - 1):
        t = i / fps
        cells = cell_diff(frames[i], last)
        mask = cells > thresh
        frac = float(mask.mean())                       # 大きく変わった升の割合
        if frac == 0:
            if t - last_t >= floor:
                keep.append((i, "floor", 0.0)); last, last_t = frames[i], t
            continue
        # 持続性: 変わった升のうち、次のフレームでも同じ内容のままの割合。
        # 炎や待機モーションは変わり続けるので低く、ツールチップ・パネルは 0.2 秒でも出ていれば高い
        stay = float((cell_diff(frames[i + 1], frames[i])[mask] < thresh).mean())
        if frac < motion_frac and stay >= persist:      # 画面の一部に表示が出た/消えた
            keep.append((i, "partial", round(frac, 2))); last, last_t = frames[i], t
        elif t - last_motion_t >= motion_interval:      # 画面全体が動いている
            keep.append((i, "motion", round(frac, 2))); last, last_t, last_motion_t = frames[i], t, t
        elif t - last_t >= floor:
            keep.append((i, "floor", round(frac, 2))); last, last_t = frames[i], t
    return keep


def extract(video, ss, to, fps, idx, times, out):
    # 走査と同じ fps フィルタの n 番目を原寸で取る（-ss の秒指定だと最大 1/fps ずれ、0.2 秒の表示を外す）
    from PIL import Image
    w, h = map(int, subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", video]).decode().strip().split(","))
    cmd = ["ffmpeg", "-v", "error"]
    if ss: cmd += ["-ss", str(ss)]
    cmd += ["-i", video]
    if to: cmd += ["-t", str(to - (ss or 0))]
    cmd += ["-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    want = {i: n for n, i in enumerate(idx)}
    size = w * h * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as p:
        i = 0
        while want:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            if i in want:
                n = want.pop(i)
                path = os.path.join(out, f"{n:04d}_{times[n]:08.2f}.png")
                Image.frombytes("RGB", (w, h), buf).save(path)
                yield path
            i += 1
        p.kill()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ss", type=float, default=0)
    ap.add_argument("--to", type=float, default=None)
    ap.add_argument("--scan-fps", type=float, default=10)
    ap.add_argument("--thresh", type=float, default=22, help="升の平均差がこれを超えたら「変わった升」（0-255）")
    ap.add_argument("--motion-frac", type=float, default=0.5, help="変わった升がこの割合以上なら画面全体の動きとみなす")
    ap.add_argument("--motion-interval", type=float, default=2, help="画面全体が動く区間で残す間隔（秒）")
    ap.add_argument("--floor", type=float, default=5, help="変化が無くてもこの秒数ごとに 1 枚残す")
    ap.add_argument("--ocr", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    frames = scan(a.video, a.ss, a.to, a.scan_fps)
    keep = pick(frames, a.scan_fps, a.thresh, a.motion_frac, a.motion_interval, a.floor)
    times = [a.ss + i / a.scan_fps for i, _, _ in keep]
    paths = list(extract(a.video, a.ss, a.to, a.scan_fps, [i for i, _, _ in keep], times, a.out))
    with open(os.path.join(a.out, "index.tsv"), "w") as f:
        f.write("file\tsec\treason\tchanged_cells\n")
        for p, t, (_, why, d) in zip(paths, times, keep):
            f.write(f"{os.path.basename(p)}\t{t:.2f}\t{why}\t{d}\n")
    kinds = {k: sum(1 for _, w, _ in keep if w == k) for k in ("partial", "motion", "floor")}
    print(f"scanned {len(frames)} frames at {a.scan_fps}fps -> kept {len(paths)} {kinds} in {a.out}")
    if a.ocr:
        ocr = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_vision.swift")
        with open(os.path.join(a.out, "ocr.txt"), "w") as f:
            for i in range(0, len(paths), 30):
                f.write(subprocess.run(["swift", ocr] + paths[i:i + 30], capture_output=True, text=True).stdout)
        print(f"ocr -> {os.path.join(a.out, 'ocr.txt')}")


if __name__ == "__main__":
    main()
