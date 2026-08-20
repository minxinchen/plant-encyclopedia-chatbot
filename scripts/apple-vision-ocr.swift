#!/usr/bin/swift

import AppKit
import Foundation
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
}

guard CommandLine.arguments.count == 2 else {
    fputs("usage: apple-vision-ocr IMAGE\n", stderr)
    exit(2)
}

let imagePath = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: imagePath),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("unable to load image\n", stderr)
    exit(3)
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
        engine: "Apple Vision VNRecognizeTextRequest accurate",
        languages: request.recognitionLanguages,
        lines: lines,
        text: lines.map(\.text).joined(separator: "\n")
    )
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    FileHandle.standardOutput.write(try encoder.encode(result))
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(4)
}
