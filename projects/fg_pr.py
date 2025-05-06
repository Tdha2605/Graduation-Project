baudrates = [57600, 9600, 115200, 38400, 19200]

for b in baudrates:
    try:
        print(f"Testing baudrate {b}...")
        from pyfingerprint.pyfingerprint import PyFingerprint
        f = PyFingerprint('/dev/ttyAMA4', b, 0xFFFFFFFF, 0x00000000)
        if f.verifyPassword():
            print(f'✅ Sensor responded at baudrate {b}')
            break
    except Exception as e:
        print(f'❌ No response at {b}: {e}')
