import pyspacemouse
import time

def main():
    """Simple test application for PySpaceMouse"""
    
    print("PySpaceMouse Test Application")
    print("=" * 40)
    
    # List available devices
    print("Checking for connected devices...")
    devices = pyspacemouse.list_devices()
    if not devices:
        print("No supported SpaceMouse devices found!")
        print("\nSupported devices:")
        for device_name, vid, pid in pyspacemouse.list_available_devices():
            print(f"  - {device_name} (VID: 0x{vid:04X}, PID: 0x{pid:04X})")
        return
    
    print(f"Found devices: {devices}")
    
    # Custom callback functions
    def position_callback(state):
        """Callback for position/orientation changes"""
        if state:
            print(f"Position: X={state.x:+6.3f} Y={state.y:+6.3f} Z={state.z:+6.3f} | "
                  f"Rotation: Roll={state.roll:+6.3f} Pitch={state.pitch:+6.3f} Yaw={state.yaw:+6.3f}")
    
    def button_callback(state, buttons):
        """Callback for button state changes"""
        pressed_buttons = [i for i, pressed in enumerate(buttons) if pressed]
        if pressed_buttons:
            print(f"Buttons pressed: {pressed_buttons}")
        else:
            print("All buttons released")
    
    # Try to open the device
    try:
        print(f"\nTrying to open: {devices[0]}")
        device = pyspacemouse.open(
            dof_callback=position_callback,
            button_callback=button_callback,
            set_nonblocking_loop=True
        )
        
        if device is None:
            print("Failed to open device!")
            return
        
        print(f"Successfully opened: {device.describe_connection()}")
        print("\nMove the SpaceMouse or press buttons to see output...")
        print("Press Ctrl+C to exit\n")
        
        # Main loop - just keep the program running
        # The callbacks will handle the output automatically
        try:
            while True:
                # Read state (this triggers the callbacks)
                state = device.read()
                time.sleep(0.01)  # Small delay to prevent overwhelming the CPU
                
        except KeyboardInterrupt:
            print("\nExiting...")
    
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        # Clean up
        try:
            pyspacemouse.close()
            print("Device closed successfully")
        except:
            pass

if __name__ == "__main__":
    main()