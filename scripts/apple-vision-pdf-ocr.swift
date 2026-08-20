#!/usr/bin/swift

import AppKit
import CryptoKit
import Foundation
import PDFKit
import Vision

struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct OCRResult: Codable {
    let engine: String
    let languages: [String]
    let lines: [OCRLine]
    let text: String
    let inputSha256: String
    let renderDpi: Int
    let pixelWidth: Int
    let pixelHeight: Int
}

func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

guard CommandLine.arguments.count == 4,
      let pageNumber = Int(CommandLine.arguments[2]),
      let renderDpi = Int(CommandLine.arguments[3]),
      pageNumber > 0,
      renderDpi > 0 else {
    fputs("usage: apple-vision-pdf-ocr PDF PAGE_NUMBER_1_BASED DPI\n", stderr)
    exit(2)
}

let pdfPath = CommandLine.arguments[1]
guard let document = PDFDocument(url: URL(fileURLWithPath: pdfPath)),
      let page = document.page(at: pageNumber - 1) else {
    fputs("unable to load PDF page\n", stderr)
    exit(3)
}

let bounds = page.bounds(for: .mediaBox)
let scale = CGFloat(renderDpi) / 72.0
let targetSize = NSSize(
    width: max(1, ceil(bounds.width * scale)),
    height: max(1, ceil(bounds.height * scale))
)
let image = page.thumbnail(of: targetSize, for: .mediaBox)
guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("unable to render PDF page\n", stderr)
    exit(4)
}

let bitmap = NSBitmapImageRep(cgImage: cgImage)
guard let pngData = bitmap.representation(using: .png, properties: [:]) else {
    fputs("unable to encode rendered page\n", stderr)
    exit(5)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["de-DE", "en-US"]

do {
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
    let observations = request.results ?? []
    let lines = observations.compactMap { observation -> OCRLine? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return OCRLine(
            text: candidate.string,
            confidence: candidate.confidence,
            x: box.origin.x,
            y: box.origin.y,
            width: box.size.width,
            height: box.size.height
        )
    }.sorted {
        if abs($0.y - $1.y) > 0.012 { return $0.y > $1.y }
        return $0.x < $1.x
    }
    let result = OCRResult(
        engine: "Apple Vision VNRecognizeTextRequest accurate / PDFKit render",
        languages: request.recognitionLanguages,
        lines: lines,
        text: lines.map(\.text).joined(separator: "\n"),
        inputSha256: sha256Hex(pngData),
        renderDpi: renderDpi,
        pixelWidth: cgImage.width,
        pixelHeight: cgImage.height
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    FileHandle.standardOutput.write(try encoder.encode(result))
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(6)
}
