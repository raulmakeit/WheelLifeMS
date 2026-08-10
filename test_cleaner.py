import re
import sys
sys.path.append(".")
from main import clean_markdown_text

def run_tests():
    test_cases = [
        (
            '*Resumen Ejecutivo de 1 Minuto*\n\n*   *El modelo de "empresa unipersonal" está...',
            '*Resumen Ejecutivo de 1 Minuto*\n\n*   *El modelo de "empresa unipersonal" está...',
            "Already clean text"
        ),
        (
            '¡Claro! Aquí tienes el artículo limpio y formateado, incluyendo el resumen ejecutivo y los créditos solicitados:\n\n***\n\n*### Resumen Ejecutivo de 1 Minuto*',
            '***\n\n### *Resumen Ejecutivo de 1 Minuto*',
            "Basic greeting with colon"
        ),
        (
            '¡Absolutamente! Aquí tienes el artículo original, cuidadosamente limpiado, estructurado y formateado en Markdown, con el resumen ejecutivo y los créditos solicitados:\n\n***Resumen Ejecutivo de 1 Minuto',
            '***Resumen Ejecutivo de 1 Minuto',
            "Complex greeting with colon and asterisks"
        ),
        (
            '¡Claro! Aquí tienes el artículo estructurado en Markdown\n\n# Título del Artículo',
            '# Título del Artículo',
            "Greeting with no colon but double newline and header"
        ),
    ]
    
    failures = 0
    for idx, (input_text, expected_output, desc) in enumerate(test_cases):
        actual = clean_markdown_text(input_text)
        if actual.strip() != expected_output.strip():
            print(f"FAIL: Test {idx} ({desc})")
            print(f"  Expected: {repr(expected_output[:60])}")
            print(f"  Got:      {repr(actual[:60])}")
            failures += 1
        else:
            print(f"PASS: Test {idx} ({desc})")
    
    if failures > 0:
        print(f"\n{failures} tests failed!")
        sys.exit(1)
    else:
        print("\nAll tests passed successfully!")

if __name__ == "__main__":
    run_tests()
