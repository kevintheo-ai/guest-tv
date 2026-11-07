// Guest TV: device configuration (the ONE place to edit before building the ipk).
// piUrl = fixed address of the Raspberry Pi in the local network (give it a DHCP reservation).
window.GUEST_TV_CONFIG = {
  piUrl: "http://192.168.1.10:8090",
  pollSeconds: 300,          // fetch state.json every 5 min
  screensaverAfterSec: 180,  // 3 min without input -> screensaver
  defaultLang: "en",
  languages: ["de", "en", "fr", "it", "nl", "pl"],
  appIds: {                  // webOS app ids: verify each one on your TV (luna://com.webos.applicationManager/listApps)
    tv: "zattoo",
    netflix: "netflix",
    prime: "amazon",
    disney: "com.disney.disneyplus-prod",
    youtube: "youtube.leanback.v4"
  }
  // TV settings (sleep timer, brightness, eye comfort, system language) are written by the Pi: piUrl + /tv/setting
};
