from core.llm_orchestrator import LLMOrchestrator, TaskTier

def test_429_triggers_fallback():
    orchestrator = LLMOrchestrator()
    print("Testing 429 mock...")
    
    # Mock rate limit on COMPLEX
    print("MOCKING RATE LIMIT ON COMPLEX TIER")
    orchestrator._mock_rate_limit(TaskTier.COMPLEX)

    # In our orchestrator, generating with retry actually calls the real API for the fallback tier (e.g. LOGIC).
    # Since we want to test if it *attempts* the logic tier, we can just call it with a simple prompt.
    prompt = "Reply with exactly one word: 'Hello'"
    try:
        result = orchestrator.generate_content_with_retry(
            TaskTier.COMPLEX, prompt
        )
        print(f"Final Model Used: {result.model_used.name}")
        assert result.model_used == TaskTier.LOGIC, "Fell back correctly, but used wrong model"
        print("Fallback test PASSED.")
    except Exception as e:
        print(f"Fallback test FAILED: {e}")
        
    orchestrator.print_usage()

if __name__ == "__main__":
    test_429_triggers_fallback()
