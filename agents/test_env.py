import sys
import os
import importlib.util

print(f"--- Environment Info ---")
print(f"Current Working Directory: {os.getcwd()}")
print(f"Python Executable: {sys.executable}")

print("\n--- sys.path (Where Python looks for modules) ---")
for p in sys.path:
    print(p)

print("\n--- Checking for 'dependency_builder' ---")
# Check if python can find the package spec
try:
    spec = importlib.util.find_spec('dependency_builder')
    if spec is None:
        print("❌ Could not find module 'dependency_builder'.")
        print("   -> Verify the folder 'dependency_builder' exists in one of the sys.path directories above.")
    else:
        print(f"✅ Found 'dependency_builder' at: {spec.origin}")
except Exception as e:
    print(f"❌ Error finding spec: {e}")

print("\n--- Detailed Import Attempt ---")
try:
    import dependency_builder
    print("1. Imported dependency_builder package successfully.")
    
    from dependency_builder import ccls_ingestion
    print("2. Imported ccls_ingestion module successfully.")
    
    from dependency_builder.ccls_ingestion import CCLSIngestion
    print("3. Imported CCLSIngestion class successfully.")
    
except ImportError as e:
    print(f"❌ Import Failed: {e}")
    print("   -> Check for circular imports or missing __init__.py files.")
except ModuleNotFoundError as e:
    print(f"❌ Module Not Found: {e}")