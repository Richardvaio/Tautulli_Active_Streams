![LOGO Github@2x@2x](https://github.com/user-attachments/assets/c3fc7c90-b1a4-4c4a-bfa0-e3542f68286f)

<br>

---

 ## ⭐ Like This Integration? 
 ``` If you find the **Tautulli Active Streams** integration helpful, please take a moment to give it a ⭐ on [GitHub] Your feedback and support help improve the project and motivate further development. Thanks! 🚀 ```
<br>

---

<br>

## 📌 Features
A custom integration for Home Assistant that allows you to monitor active Plex streams and User Statistics using Tautulli Api.**Now integrated Plex Token** for even more attributes for your Plex sessions!
<br>
*Multi Tautulli Instances Supported!*

##  Active Streams

- Dynamically creates and removes session sensors based on active Plex streams.
- Adjustable Scan Interval – Set how often HA updates stream data.
- Detailed Session Attributes – Each active stream sensor provides:
    * :film_strip: **Media Title & Type** (Movie, TV Show, etc.)
    * :bust_in_silhouette: **User** (Who is watching)
    * :earth_africa: **IP Address & Device**
    * :tv: **Playback Progress & Quality**
      * And so much more!

 
## 🆕 Plex Extended Features & Attributes

If you enable **Plex Integration** and supply your **Plex Token** in the integration setup, you can take advantage of advanced session data:
- **Enhanced Session Attributes**  
    * Directors  
    * Writers  
    * Genres  
    * External IDs (IMDB, TMDb, TVDb)  
    * Tagline  
    * Summary  
    * Studio  
    * Content Rating  
    * Rating  
    * Audience Rating  
    * Originally Available At  
    * Rating Key  
    * In Credits  
    * Credits Start Time  


##  Statistics

- Separate Scan Interval  – Set how often HA updates Statistics data.
- Fetch Range - Choose how far data should be fetched from default 30 days ago. 
- Detailed User Attributes – Each user sensor provides:
    * **Total Play Duration**
    * **Total Completion Rate**
    * **Longest Play**
    * **Preferred Watch Time**
    * **Transcode Percentage**
    * **Geo Location Data**
      * And so much more!

 
--- 

## 📌 Services

   * **Kill All Active Streams:**  
   A new service that terminates every active stream, regardless of the user.
   * **Kill Active Streams by Username:**  
   A new service that terminates all streams associated with a specified username.
   * **Kill Active Streams by Session:**  
   A new service that terminates a Single stream.
   
<details>
<summary>➡️ Click Here  🛠️  How to Use the Kill Stream Services</summary>
 
<br>
Check Attributes in "plex_session_" for "user" or "session_id"
<br>
 
### 1️⃣ Kill All Active Streams
Call the service from Developer Tools → Actions:

```yaml
service: tautulli_active_streams.kill_all_streams
data:
  message: "Your Message Here"
```
---
### 2️⃣ Kill Streams for a Specific User
Call the service for a specific user:

```yaml
service: tautulli_active_streams.kill_user_streams
data:
  user: "john_doe"
  message: "Your Message Here."
```
---
### 2️⃣ Kill Streams for a Specific Session
Call the service for a specific session_id:

```yaml
service: tautulli_active_streams.kill_session_stream
data:
  session_id: "gxvzdoq4fjkjfmduq5dgf25hz"
  message: "Your Message Here."
```

</pre>
</details>

These new features are designed to work seamlessly with automations and will become even more powerful as additional data becomes available in future updates.




---



---
![Tautulli Active Streams - Card - Short](https://github.com/user-attachments/assets/451a6abf-4bd4-414d-bfd1-28be0f272734)
---

:link: **GitHub:** [Tautulli Active Streams ](https://github.com/Richardvaio/Tautulli_Active_Streams)

:link: **Home-assistant Community:** (https://community.home-assistant.io/t/custom-component-tautulli-active-streams)

<br>

---


## **:inbox_tray: Installation**

* **Via HACS:**
  * Open [HACS](https://hacs.xyz/) in Home Assistant.
  * Click the three dots in the top right corner and select Custom repositories.
  * Add the repository URL for this integration and choose Integration as the category.
  * Download the integration from HACS.
  
* **Manual Installation:**
  * Download the repository from GitHub.
  * Place the entire folder into your `custom_components/tautulli_active_streams` directory.
   
* **Setup in Home Assistant:**
  * Go to **Settings → Devices & Services** and **Add** the integration.
  * Enter your **Tautulli details (URL, API Key)**, 
  * Set the Refresh Interval.

  * (Optional) Enable:
    - Image Proxy Service
    - IP Geolocation
    - Advanced Attributes
    - Plex Integration
    - Statistics Service
---

# Don't forget to add the Card below.

My usage: I have a chip card that displays the current active session count. 
When clicked, It will trigger a bubble popup card where all active sessions can be viewed.


<br>




## 🚀Custom Lovelace card
Produced by @stratotally and Edited by me.
Dynamically displays active Plex sessions using **Tautulli** data fetched by the `Tautulli Active Streams` integration.

1. **Install** `Tautulli Active Streams` integration via HACS or manually.
2. **Ensure Lovelace resources** for `auto-entities`, `button-card`, `bar-card`, `card-mod` are loaded.
3. **Copy the YAML below** into your Lovelace dashboard. 
4. **Enjoy real-time Plex session monitoring!** 🎬🔥

<br>

<details>
<summary>➡️ Click Here  Card YAML - Last updated 23.02.2025</summary>
<br><br> 
 
```yaml
Cards now in repo, one for Movies/Tv shows and one for Music only 
```
!</pre>
</details>

<br>

---


## 🛠 Troubleshooting

### No Data Appearing?

- Ensure your **Tautulli API key, Url** are correct.
- Restart Home Assistant after making changes.
- Check **Developer Tools** → **States** for `sensor.plex_session_*`.

<br>

---

## 🤝 Contributing
Want to improve this integration? Feel free to submit a PR or open an issue on GitHub!

---

## 📜 License
This project is licensed under the MIT License.


---

---
