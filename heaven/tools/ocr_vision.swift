// macOS Vision で画像の文字を認識する（日本語対応）。使い方: swift ocr.swift img1.png [img2.png ...]
import Foundation
import Vision
import AppKit
for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { print("## \(path)\tERR"); continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ja-JP", "en-US"]
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    print("## \(path)")
    for o in (req.results ?? []) {
        if let t = o.topCandidates(1).first {
            let b = o.boundingBox
            print(String(format: "%.3f,%.3f\t", b.minX, 1 - b.maxY) + t.string)
        }
    }
}
