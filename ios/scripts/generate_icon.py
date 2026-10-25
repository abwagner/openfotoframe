"""Render the app's simple geometric frame icon. Uses the backend's Pillow dependency."""
import json
from pathlib import Path

from PIL import Image, ImageDraw

assets = Path(__file__).resolve().parents[1] / "OpenFotoFrame" / "Assets.xcassets"
icons = assets / "AppIcon.appiconset"
icons.mkdir(parents=True, exist_ok=True)
image = Image.new("RGB", (1024, 1024), "#087f7b")
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((170, 195, 854, 829), radius=52, fill="#fcf5e8")
draw.rounded_rectangle((213, 238, 811, 786), radius=20, fill="#153f43")
draw.rounded_rectangle((246, 271, 778, 753), radius=8, fill="#dcefea")
draw.ellipse((590, 325, 684, 419), fill="#e9b35b")
draw.polygon([(246, 664), (441, 416), (649, 753), (246, 753)], fill="#61aaa0")
draw.polygon([(398, 753), (605, 479), (778, 673), (778, 753)], fill="#246a68")
image.save(icons / "AppIcon.png")
(assets / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n")
(icons / "Contents.json").write_text(json.dumps({
    "images": [{"filename": "AppIcon.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}],
    "info": {"author": "xcode", "version": 1},
}, indent=2) + "\n")
