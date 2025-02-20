import express from "express";

import db from "../db/connection.js";

import { ObjectId } from "mongodb";

const router = express.Router();

router.post("/upload", async (req, res) => {
  console.log("Request received");
  const jsonData = req.body;
  
  if (!jsonData || jsonData.length === 0) {
    return res.status(400).json({ message: "No data provided" });
  }

  try {
    console.log("Inserting data into MongoDB");
    console.log(jsonData);
    const result = await db.collection("records").insertMany(jsonData);
    res.status(200).json({ message: "Data uploaded successfully", result });
  } catch (err) {
    console.error("Error inserting data:", err);
    res.status(500).json({ message: "Error inserting data", error: err.message });
  }
});

router.get("/", async (req, res) => {
	let collection = await db.collection("records");
	let searchQuery = req.query.search;
	let filter = req.query?.filter;
	
	let query = {};
	if (searchQuery) {
		query = {
			...query,
			$or: [
				{ name: { $regex: searchQuery, $options: "i" } },
				{ position: { $regex: searchQuery, $options: "i" } },
			],
		};
	}
	if(filter && filter != ''){
    filter = filter.split(',');
		query = {
			...query,
			level: {
				$in: filter
			}
		}
	}
	let results = await collection.find(query).toArray();
	res.send(results).status(200);
});

router.get("/:id", async (req, res) => {
  let collection = await db.collection("records");
  let query = { _id: new ObjectId(req.params.id) };
  let result = await collection.findOne(query);

  if (!result) res.send("Not found").status(404);
  else res.send(result).status(200);
});

router.post("/", async (req, res) => {
  try {
    let newDocument = {
      name: req.body.name,
      position: req.body.position,
      level: req.body.level,
    };
    let collection = await db.collection("records");
    let result = await collection.insertOne(newDocument);
    res.send(result).status(204);
  } catch (err) {
    console.error(err);
    res.status(500).send("Error adding record");
  }
});

router.patch("/:id", async (req, res) => {
  try {
    const query = { _id: new ObjectId(req.params.id) };
    const updates = {
      $set: {
        name: req.body.name,
        position: req.body.position,
        level: req.body.level,
      },
    };

    let collection = await db.collection("records");
    let result = await collection.updateOne(query, updates);
    res.send(result).status(200);
  } catch (err) {
    console.error(err);
    res.status(500).send("Error updating record");
  }
});

router.delete('/delete', async (req, res) => {
    try {
        const ids = req.body.ids;

        if (!Array.isArray(ids) || ids.length === 0) {
            return res.status(400).send('Invalid or empty list of IDs');
        }

        const objectIds = ids
            .filter(id => ObjectId.isValid(id))
            .map(id => new ObjectId(id));

        if (objectIds.length === 0) {
            return res.status(400).send('No valid IDs provided');
        }

        const result = await db.collection('records').deleteMany({ _id: { $in: objectIds } });

        if (result.deletedCount === 0) {
            return res.status(404).send('No records found for the provided IDs');
        }

        res.status(200).send({ deletedCount: result.deletedCount });
    } catch (error) {
        console.error(error);
        res.status(500).send('Error deleting records');
    }
});

router.delete("/:id", async (req, res) => {
  try {
    const query = { _id: new ObjectId(req.params.id) };

    const collection = db.collection("records");
    let result = await collection.deleteOne(query);

    res.send(result).status(200);
  } catch (err) {
    console.error(err);
    res.status(500).send("Error deleting record");
  }
});

export default router;
