Ingestion Basic:
----------------
1. Read using Fitz.
2. Identify each structure - Headings, Text, lines, image, pagenumber, headerfooter, etc.
3. All important functions of fitz are in important_functions.md
4. Normalize coordinates
    - PDF coordinates differ across pages.
    - Store everything in a normalized structure: page_number, bbox (x0, y0, x1, y1), width, height etc. So that we can have a relative layout position. 
        - Example, 12% from left rather than (0, 76).
        - Because (0, 76) might mean one position for one page size and a different position for another page size.
    -This lets us detect: headers, footers, captions, objects near images etc.
5. Then maintain the global read order.
    - Every page has its own reading order. The order in which elements appear in the pdf.
    - When we combine all the pages, we need to maintain a global reading order.
    - Each page starts its own reading order from 0.
    - If we combine pages directly, page 2 can also have index 0, 1, 2...
        which overlaps with page 1.
    - Stride is a fixed gap we reserve for each page.
    - Example with stride = 100000:
        - page 1 uses 0..99999
        - page 2 uses 100000..199999
        - page 3 uses 200000..299999
    - This guarantees later pages always have larger reading-order values
        than earlier pages.


Section Detection:
-------------------
1. So we have identified the page elements of each page from ingestion basic.
2. Next we need to get all the headings of each page.
3. Then build sections. From one heading to the next is one section.
4. Once we detect sections, we can let the user decide which ones to keep and which ones to remove.
5. For now, we can ask for the required sections in the terminal. Since there are a lot of sections, we should be able to give it groups. Kind of like, keep section (1, 5), (10, 12).
    - Basically pass in tuples.
6. For development purposes, we can keep the section detection for DDIA book in a file and use that directly.
7. Lets group all elements from one header to the next into a section. Lets number the sections.
8. The same page might have multiple sections right? So we are not getting elements from the same page into sections. Thats incorrect.
    We need to use read_order_index, all elements starting from the reading order of heading including the heading itself all the way to the reading order of another heading.


Noise Removal:
-------------------------
1. Header and Footer may have noise. Unwanted images, and all that may act as noise.
2. We need to eliminate those things from each chapter, only keep whats required in the chapter.
3. Can happen parallely for each chapter.


Extract every element present in the chapter:
---------------------------------------------
1. Extract each element such as text, image, tables, code blocks, etc from the chapter.
2. We need to create directories to store these.
3. Later on we need Computer Vision to generate scenes from them.


Association:
-------------
1. We need to know if a text is talking about some image.
2. We need to know if a text is talking about some table.
3. We need to know if a text is talking about some code.
4. We need to know if a text is talking about some graph.



