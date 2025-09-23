#!/usr/bin/env python3
"""
Script per testare l'app su Render e diagnosticare problemi
"""
import requests
import time
import json

# Sostituisci con il tuo URL Render - lo trovi su Render Dashboard
RENDER_URL = "https://referto-app.onrender.com"  # CAMBIA QUESTO!

def test_endpoints():
    print("🧪 Test endpoints Render...")
    
    # Test 1: Health check
    try:
        print("\n1️⃣ Test Health Check...")
        response = requests.get(f"{RENDER_URL}/health", timeout=30)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
    except Exception as e:
        print(f"❌ Health check fallito: {e}")
        return False
    
    # Test 2: Home page
    try:
        print("\n2️⃣ Test Home Page...")
        response = requests.get(RENDER_URL, timeout=30)
        print(f"Status: {response.status_code}")
        print(f"Content length: {len(response.text)} chars")
    except Exception as e:
        print(f"❌ Home page fallita: {e}")
        return False
    
    # Test 3: Upload piccolo
    try:
        print("\n3️⃣ Test Upload Testo...")
        # Crea file di test piccolo
        test_content = "Referto di test\nPaziente: Mario Rossi\nEsami del sangue: tutto nella norma"
        files = {'file': ('test.txt', test_content.encode(), 'text/plain')}
        
        response = requests.post(f"{RENDER_URL}/upload", files=files, timeout=45)
        print(f"Upload Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"Task ID: {data.get('task_id')}")
            
            # Controlla status
            if 'task_id' in data:
                task_id = data['task_id']
                for i in range(10):  # Aspetta max 30 secondi
                    time.sleep(3)
                    status_resp = requests.get(f"{RENDER_URL}/check_status/{task_id}", timeout=15)
                    status_data = status_resp.json()
                    print(f"Status check {i+1}: {status_data.get('status')}")
                    
                    if status_data.get('status') == 'completed':
                        print(f"✅ Elaborazione completata!")
                        print(f"Risultato: {status_data.get('result', '')[:200]}...")
                        break
                    elif status_data.get('status') == 'error':
                        print(f"❌ Errore: {status_data.get('result')}")
                        break
        
    except Exception as e:
        print(f"❌ Test upload fallito: {e}")
        return False
    
    print("\n✅ Test completati!")
    return True

def monitor_cold_start():
    print("\n🕐 Test Cold Start...")
    start_time = time.time()
    
    try:
        response = requests.get(f"{RENDER_URL}/health", timeout=60)
        end_time = time.time()
        
        print(f"Cold start time: {end_time - start_time:.2f} secondi")
        print(f"Status: {response.status_code}")
        
    except Exception as e:
        print(f"❌ Cold start test fallito: {e}")

if __name__ == "__main__":
    print("🚀 Test Render App")
    print(f"URL: {RENDER_URL}")
    print("-" * 50)
    
    # Test cold start
    monitor_cold_start()
    
    # Test funzionalità
    test_endpoints()
    
    print("\n🏁 Test completati!")
    print("\nSe vedi errori, controlla i log su Render Dashboard.")
