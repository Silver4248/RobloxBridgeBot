const express = require('express');
const axios = require('axios');
const app = express();
const port = 3000;

// 🔐 YOUR PRIVATE API KEY - NEVER SHARE THIS
const API_KEY = 'your-api-key-here';

// Replace this with your group ID
const GROUP_ID = '12345678';

app.get('/group-info', async (req, res) => {
    try {
        const response = await axios.get(`https://apis.roblox.com/groups/v1/groups/${GROUP_ID}`, {
            headers: {
                'x-api-key': API_KEY
            }
        });

        res.json({
            name: response.data.name,
            description: response.data.description,
            owner: response.data.owner ? response.data.owner.displayName : 'N/A'
        });
    } catch (error) {
        console.error('Error:', error.response?.data || error.message);
        res.status(500).json({ error: 'Failed to fetch group info.' });
    }
});

app.listen(port, () => {
    console.log(`Server is running at http://localhost:${port}`);
});
