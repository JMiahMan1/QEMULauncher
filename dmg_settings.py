
filename = "QEMU-Launcher-Universal.dmg"
volume_name = "QEMU Launcher"
format = "UDBZ"
size = "500M"

files = [
    "QEMU Launcher.app"
]

symlinks = {
    "Applications": "/Applications"
}

icon = "RunLinux.icns"

badge_icon = "RunLinux.icns"

# Positioning
icon_locations = {
    "QEMU Launcher.app": (140, 120),
    "Applications": (460, 120)
}

window_rect = ((100, 100), (600, 400))
background = "builtin-arrow"
