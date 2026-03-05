import sys
import os

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

def test_stripe_module_import():
    try:
        from subscriptions.stripe import create_subscription_with_saved_card
        assert create_subscription_with_saved_card is not None
        print("✅ subscriptions.stripe imported successfully")
    except ImportError as e:
        print(f"❌ ImportError: {e}")
        assert False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        assert False
