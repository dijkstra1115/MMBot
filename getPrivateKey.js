// --- 🐒 Monkey Patch 攔截陷阱 Start ---
const originalGenerateKey = window.crypto.subtle.generateKey;
const originalImportKey = window.crypto.subtle.importKey;

// 1. 攔截 generateKey (生成新鑰匙時觸發)
window.crypto.subtle.generateKey = async function (...args) {
    console.log("%c🪤 偵測到鑰匙生成請求！正在修改參數...", "color: orange; font-weight: bold;");
    
    // 強制把 extractable (第二個參數) 改為 true
    if (args.length > 1) {
        args[1] = true; 
        console.log("✅ 已強制設定 extractable = true");
    }

    // 執行原始生成動作
    const result = await originalGenerateKey.apply(this, args);

    // 偷看鑰匙內容
    try {
        if (result.privateKey) {
            const exported = await window.crypto.subtle.exportKey("jwk", result.privateKey);
            console.log("%c🎉 成功捕獲 Private Key (JWK):", "color: green; font-size: 16px; font-weight: bold;");
            console.log(JSON.stringify(exported, null, 2));
            
            // 如果需要 hex 格式 (給 Python 用)，這裡嘗試轉換
            // 注意：不同演算法 (Ed25519 vs ECDSA) 格式不同，這裡是通用的
            console.log("%c👉 請檢查上面的 'd' 或 'x' 欄位，這通常是私鑰的原始數據。", "color: blue;");
        }
    } catch (e) {
        console.error("❌ 攔截後匯出失敗:", e);
    }
    
    return result;
};

console.log("%c🐵 陷阱已佈署！請現在去點擊 'Enable Trading' 或 'Sign' 按鈕...", "color: red; font-size: 20px; background: yellow;");
// --- 🐒 Monkey Patch 攔截陷阱 End ---