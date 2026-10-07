"""Spoken call scripts for every supported voice-enrollment language."""
from .confidence import DECLINE

LANGUAGE_NAMES = {'en': 'English', 'es': 'Spanish', 'hi': 'Hindi', 'ru': 'Russian'}

OPENINGS = {
    'en': 'Hello, My name is VeriVoice AI. Before we can chat, I need to verify that you are the owner of this account. To confirm your identity, repeat after me: With VeriVoice, my voice is my password.',
    'es': 'Hola, me llamo VeriVoice AI. Antes de conversar, necesito verificar que usted es la persona titular de esta cuenta. Para confirmar su identidad, repita después de mí: Con VeriVoice, mi voz es mi contraseña.',
    'hi': 'नमस्ते, मेरा नाम VeriVoice AI है। बातचीत शुरू करने से पहले, मुझे यह सत्यापित करना होगा कि आप इस खाते के मालिक हैं। अपनी पहचान की पुष्टि करने के लिए मेरे बाद दोहराएँ: VeriVoice के साथ, मेरी आवाज़ मेरा पासवर्ड है।',
    'ru': 'Здравствуйте, меня зовут VeriVoice AI. Прежде чем мы начнём разговор, мне нужно проверить, что вы являетесь владельцем этого аккаунта. Чтобы подтвердить свою личность, повторите за мной: С VeriVoice мой голос — мой пароль.',
}

BANKER_OPENINGS = {
    'en': 'Your voice has been verified. I am a pretend banker at the imaginary firm Satoshi Bank. All services and balances here are imaginary. You can withdraw or deposit ByteCoins, invest ByteCoins in a meme coin or meme stock, or spend ByteCoins to increase your imaginary VeriVoice AI token balance. What would you like to do with your imaginary bank account?',
    'es': 'Su voz ha sido verificada. Soy un banquero de ficción en la empresa imaginaria Satoshi Bank. Todos los servicios y saldos aquí son imaginarios. Puede retirar o depositar ByteCoins, invertir ByteCoins en una moneda o acción meme, o gastar ByteCoins para aumentar su saldo imaginario de tokens de VeriVoice AI. ¿Qué le gustaría hacer con su cuenta bancaria imaginaria?',
    'hi': 'आपकी आवाज़ सत्यापित हो गई है। मैं काल्पनिक संस्था Satoshi Bank में एक काल्पनिक बैंक कर्मचारी की भूमिका निभा रहा हूँ। यहाँ सभी सेवाएँ और खाते के शेष काल्पनिक हैं। आप ByteCoins निकाल सकते हैं या जमा कर सकते हैं, किसी मीम कॉइन या मीम शेयर में ByteCoins निवेश कर सकते हैं, या VeriVoice AI के अपने काल्पनिक टोकन शेष को बढ़ाने के लिए ByteCoins खर्च कर सकते हैं। आप अपने काल्पनिक बैंक खाते में कौन-सी सेवा लेना चाहेंगे?',
    'ru': 'Ваш голос подтверждён. Я играю роль вымышленного банковского сотрудника в воображаемой компании Satoshi Bank. Все услуги и средства здесь воображаемые. Вы можете снять или внести ByteCoins, вложить ByteCoins в мемную монету или мемную акцию либо потратить ByteCoins, чтобы увеличить свой воображаемый баланс токенов VeriVoice AI. Что вы хотели бы сделать со своим воображаемым банковским счётом?',
}

FAREWELLS = {
    'en': DECLINE,
    'es': 'Lo siento, para continuar esta llamada debe registrar su número de teléfono en una cuenta de VeriVoice AI. Gracias y que tenga un buen día.',
    'hi': 'माफ़ कीजिए, इस कॉल को जारी रखने के लिए आपको अपना फ़ोन नंबर VeriVoice AI खाते में पंजीकृत करना होगा। धन्यवाद, आपका दिन शुभ हो।',
    'ru': 'Извините, чтобы продолжить этот звонок, вам необходимо зарегистрировать свой номер телефона в аккаунте VeriVoice AI. Спасибо и хорошего дня.',
}
