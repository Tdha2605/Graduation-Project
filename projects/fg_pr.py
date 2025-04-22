from pyfingerprint.pyfingerprint import PyFingerprint
import base64
import time
import sys

def init_sensor():
    try:
        sensor = PyFingerprint('/dev/ttyAMA0', 57600, 0xFFFFFFFF, 0x00000000)
        if not sensor.verifyPassword():
            raise Exception('Sensor password is incorrect')
        print('✅ Sensor connected.')
        return sensor
    except Exception as e:
        print('❌ Error initializing sensor:', str(e))
        sys.exit(1)

def enroll_and_show_base64(sensor):
    try:
        print('\n👉 Place your finger (1st scan)...')
        while not sensor.readImage():
            time.sleep(0.1)
        sensor.convertImage(0x01)

        # ✅ Export base64 after first scan (before saving)
        data = sensor.downloadCharacteristics()
        b64 = base64.b64encode(bytes(data)).decode('utf-8')
        print(f'📦 Base64 of 1st scan:\n{b64}\n')

        result = sensor.searchTemplate()
        if result[0] >= 0:
            print(f'⚠️ Fingerprint already exists at position #{result[0]}')
            return

        print('✅ First scan complete. Remove your finger...')
        time.sleep(2)

        print('👉 Place the same finger again (2nd scan)...')
        while not sensor.readImage():
            time.sleep(0.1)
        sensor.convertImage(0x02)

        if sensor.compareCharacteristics() == 0:
            print('❌ Fingerprints do not match.')
            return

        sensor.createTemplate()
        position = sensor.storeTemplate()
        print(f'✅ Fingerprint stored at position #{position}')

    except Exception as e:
        print('❌ Error during enrollment:', str(e))


def view_all_templates(sensor):
    try:
        capacity = sensor.getStorageCapacity()
        total = 0
        print(f'\n📦 Sensor capacity: {capacity} slots')

        for page in range(0, (capacity + 255) // 256):
            index = sensor.getTemplateIndex(page)
            for i in range(256):
                pos = page * 256 + i
                if pos >= capacity:
                    break
                if index[i]:
                    print(f'\n📍 Template found at position #{pos}')
                    sensor.loadTemplate(pos, 0x01)
                    data = sensor.downloadCharacteristics()
                    b64 = base64.b64encode(bytes(data)).decode('utf-8')
                    print(f'Base64:\n{b64}')
                    total += 1

        if total == 0:
            print('ℹ️ No templates found.')

        print(f'\n✅ Total templates: {total}')
    except Exception as e:
        print('❌ Error while reading templates:', str(e))

def delete_all_templates(sensor):
    try:
        capacity = sensor.getStorageCapacity()
        deleted = 0

        for page in range(0, (capacity + 255) // 256):
            index = sensor.getTemplateIndex(page)
            for i in range(256):
                pos = page * 256 + i
                if pos >= capacity:
                    break
                if index[i]:
                    sensor.deleteTemplate(pos)
                    print(f'🗑️ Deleted template at position #{pos}')
                    deleted += 1

        print(f'\n✅ All templates deleted: {deleted} total')

    except Exception as e:
        print('❌ Error while deleting templates:', str(e))

def delete_template_by_position(sensor):
    try:
        pos = int(input('🔢 Enter the template position to delete (e.g. 0–511): '))
        sensor.deleteTemplate(pos)
        print(f'✅ Template at position #{pos} has been deleted.')
    except Exception as e:
        print('❌ Error deleting template:', str(e))

def verify_fingerprint(sensor):
    try:
        print('\n👉 Place your finger to verify...')
        while not sensor.readImage():
            time.sleep(0.1)
        sensor.convertImage(0x01)

        result = sensor.searchTemplate()
        position = result[0]
        score = result[1]

        if position == -1:
            print('❌ No matching fingerprint found.')
        else:
            print(f'✅ Match found at position #{position}, score: {score}')

    except Exception as e:
        print('❌ Error during verification:', str(e))

def main():
    sensor = init_sensor()

    while True:
        print('\n===== Fingerprint Manager =====')
        print('1. Enroll new fingerprint and show base64')
        print('2. View all stored templates (base64)')
        print('3. Delete all templates')
        print('4. Verify fingerprint')
        print('5. Delete template by position')
        print('6. Exit')
        choice = input('Select an option (1–6): ')

        if choice == '1':
            enroll_and_show_base64(sensor)
        elif choice == '2':
            view_all_templates(sensor)
        elif choice == '3':
            confirm = input('⚠️ Are you sure you want to delete all templates? (yes/no): ')
            if confirm.lower() == 'yes':
                delete_all_templates(sensor)
        elif choice == '4':
            verify_fingerprint(sensor)
        elif choice == '5':
            delete_template_by_position(sensor)
        elif choice == '6':
            print('👋 Exiting.')
            break
        else:
            print('⚠️ Invalid choice.')

if __name__ == '__main__':
    main()
